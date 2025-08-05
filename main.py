import os
import asyncio
import google.generativeai as genai
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Markdown, ListView, ListItem, Label, TextArea, Input, Button
from textual.containers import Container, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.events import Key, Event
from dataclasses import dataclass
import re

API_KEY_FILE = ".gemini_api_key"

class APIKeyInputScreen(ModalScreen):
    def compose(self) -> ComposeResult:
        with Container(id="api-key-dialog-container"):
            yield Label("Please enter your Google Gemini API Key:")
            yield Input(placeholder="YOUR_API_KEY_HERE", password=True, id="api-key-input")
            yield Button("Save Key", variant="primary", id="save-api-key")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-api-key":
            api_key = self.query_one("#api-key-input", Input).value
            if api_key:
                self.dismiss(api_key)
            else:
                self.dismiss(None)

class RenameNoteDialog(ModalScreen):
    def __init__(self, current_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Container(id="rename-dialog-container"):
            yield Label(f"Rename '{self.current_name}' to:")
            yield Input(value=self.current_name, id="new-name-input")
            with Container(id="rename-dialog-buttons"):
                yield Button("OK", variant="primary", id="rename-ok")
                yield Button("Cancel", id="rename-cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "rename-ok":
            new_name = self.query_one("#new-name-input", Input).value
            self.dismiss(new_name)
        elif event.button.id == "rename-cancel":
            self.dismiss(None)

class NoteEditor(TextArea):
    def on_key(self, event: Key) -> None:
        if event.key == "ctrl+d":
            event.prevent_default()

class GeminiInput(TextArea):
    @dataclass
    class Submitted(Event):
        """Posted when the Gemini input is submitted (Enter key pressed)."""
        value: str

    def on_key(self, event: Key) -> None:
        """Handle key presses for the Gemini input."""
        if event.key == "enter":
            event.prevent_default()
            self.post_message(self.Submitted(self.text))

class GeminiVimApp(App):
    # Set the default theme here from the textual-themes list
    DEFAULT_THEME = "catppuccin-mocha"

    BINDINGS = [
        # The 'd' for dark mode is removed, as Ctrl+P -> Themes is the best way to switch
        ("n", "new_note", "New Note"),
        ("ctrl+s", "save_note", "Save Note"),
        ("ctrl+d", "delete_note", "Delete Note"),
        ("tab", "focus_next", "Focus Next"),
        ("shift+tab", "focus_previous", "Focus Previous"),
        ("ctrl+f", "forget_api_key", "Forget API Key"),
        ("ctrl+e", "enter_api_key", "Enter API Key"),
    ]

    SCREENS = {"api_key_input": APIKeyInputScreen}
    CSS_PATH = "main.tcss"
    LOG_PATH = "gemini_vim.log"
    
    current_note_path = reactive(None)
    editor_dirty = reactive(False)

    async def _handle_api_key_prompt(self) -> None:
        if os.path.exists(API_KEY_FILE):
            with open(API_KEY_FILE, "r") as f:
                api_key = f.read().strip()
            if api_key:
                genai.configure(api_key=api_key)
                self.query_one(Footer).message = "Gemini API key loaded."
            else:
                await self.action_forget_api_key(update_footer=False)
                self.query_one(Footer).message = "API key file was empty. Press Ctrl+E to enter a new key."
        else:
            await self._run_enter_api_key_flow(is_first_run=True)

    async def on_mount(self) -> None:
        await self.load_notes()
        self.query_one("#notes-list", ListView).focus()
        self.run_worker(self._handle_api_key_prompt, exclusive=True)

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id == "note-editor":
            self.editor_dirty = True

    async def load_notes(self) -> None:
        notes_path = "notes"
        if not os.path.exists(notes_path):
            os.makedirs(notes_path)

        notes_list_view = self.query_one("#notes-list", ListView)
        await notes_list_view.clear()

        new_note_item = ListItem(Label("New Note (unsaved)"), id="new-note-item")
        await notes_list_view.append(new_note_item)

        highlight_index = 0
        
        sorted_notes = sorted(os.listdir(notes_path))
        for index, filename in enumerate(sorted_notes, start=1):
            full_path = os.path.join(notes_path, filename)
            if os.path.isfile(full_path):
                safe_id = filename.replace('.', '_')
                item = ListItem(Label(filename), id=f"note-{safe_id}")
                await notes_list_view.append(item)
                if self.current_note_path == full_path:
                    highlight_index = index
        
        notes_list_view.index = highlight_index

    def compose(self) -> ComposeResult:
        yield Header()
        with Container(id="app-grid"):
            with VerticalScroll(id="notes-panel"):
                yield Label("Notes", classes="panel-title")
                yield ListView(id="notes-list")
            with Container(id="editor-panel"):
                yield Label("Editor", classes="panel-title", id="editor-title")
                yield NoteEditor(id="note-editor")
            with VerticalScroll(id="gemini-panel"):
                yield Label("Gemini Input", classes="panel-title")
                yield GeminiInput(id="gemini-input")
                yield Label("Gemini Output", classes="panel-title")
                with VerticalScroll(id="gemini-output-scrollable"):
                    yield Markdown("Gemini response will appear here.", id="gemini-output")
        yield Footer()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "notes-list":
            editor = self.query_one("#note-editor", NoteEditor)
            if getattr(event.item, 'id', None) == "new-note-item":
                await self.action_new_note()
            else:
                selected_note_name = event.item.query_one(Label).renderable
                self.current_note_path = os.path.join("notes", str(selected_note_name))
                self.load_note_content()
                editor.focus()

    def load_note_content(self) -> None:
        editor = self.query_one("#note-editor", NoteEditor)
        if self.current_note_path and os.path.exists(self.current_note_path):
            with open(self.current_note_path, "r") as f:
                content = f.read()
            editor.load_text(content)
        else:
            editor.load_text("")
        self.editor_dirty = False

    async def action_new_note(self) -> None:
        self.current_note_path = None
        editor = self.query_one("#note-editor", NoteEditor)
        editor.load_text("")
        editor.focus()
        self.query_one(Footer).message = "New note started."
        await self.load_notes()
        self.editor_dirty = False

    async def action_save_note(self) -> None:
        editor_content = self.query_one("#note-editor", NoteEditor).text
        if not editor_content:
            self.query_one(Footer).message = "Cannot save an empty note."
            return

        notes_dir = "notes"
        
        first_line = editor_content.splitlines()[0].strip()
        sanitized_filename = re.sub(r'[\\/:*?"<>|\s]+', '_', first_line)
        sanitized_filename = sanitized_filename.strip('_')

        proposed_filename = f"{sanitized_filename}.txt" if sanitized_filename else "new_note.txt"
        
        if self.current_note_path is None:
            proposed_path = os.path.join(notes_dir, proposed_filename)
            counter = 1
            while os.path.exists(proposed_path):
                base, ext = os.path.splitext(proposed_filename)
                proposed_path = os.path.join(notes_dir, f"{base}_{counter}{ext}")
                counter += 1
            self.current_note_path = proposed_path
        else:
            current_filename = os.path.basename(self.current_note_path)
            if proposed_filename != current_filename:
                new_path = os.path.join(notes_dir, proposed_filename)
                if os.path.exists(new_path):
                    self.query_one(Footer).message = f"Note '{proposed_filename}' already exists."
                    return
                
                try:
                    os.rename(self.current_note_path, new_path)
                    self.current_note_path = new_path
                except OSError as e:
                    self.query_one(Footer).message = f"Error renaming note: {e}"
                    self.log(f"Error renaming note: {e}")
                    return

        with open(self.current_note_path, "w") as f:
            f.write(editor_content)
        
        self.editor_dirty = False
        await self.load_notes()
        self.query_one(Footer).message = f"Note saved: {os.path.basename(self.current_note_path)}"

    async def action_delete_note(self) -> None:
        """Deletes the currently highlighted note in the list."""
        notes_list_view = self.query_one("#notes-list", ListView)
        highlighted_item = notes_list_view.highlighted_child

        if highlighted_item and getattr(highlighted_item, 'id', None) != "new-note-item":
            note_filename = highlighted_item.query_one(Label).renderable
            path_to_delete = os.path.join("notes", str(note_filename))

            if os.path.exists(path_to_delete):
                os.remove(path_to_delete)

                if path_to_delete == self.current_note_path:
                    self.current_note_path = None
                    self.query_one("#note-editor", NoteEditor).load_text("")
                    self.editor_dirty = False

                await self.load_notes()
                self.query_one(Footer).message = f"Note '{note_filename}' deleted."
        else:
            self.query_one(Footer).message = "No saved note highlighted to delete."

    async def action_rename_note(self) -> None:
        if self.current_note_path:
            current_name = os.path.basename(self.current_note_path)
            new_name = await self.push_screen_wait(RenameNoteDialog(current_name))
            if new_name and new_name != current_name:
                new_path = os.path.join(os.path.dirname(self.current_note_path), new_name)
                try:
                    os.rename(self.current_note_path, new_path)
                    self.current_note_path = new_path
                    await self.load_notes()
                    self.load_note_content()
                    self.query_one(Footer).message = f"Note renamed to: {os.path.basename(self.current_note_path)}"
                except OSError as e:
                    self.query_one(Footer).message = f"Error renaming note: {e}"
                    self.log(f"Error renaming note: {e}")

    async def on_gemini_input_submitted(self, message: GeminiInput.Submitted) -> None:
        gemini_input_text = message.value
        if not gemini_input_text.strip():
            return
        
        gemini_output = self.query_one("#gemini-output", Markdown)
        gemini_output.update("Thinking...")

        try:
            model = genai.GenerativeModel('models/gemini-1.5-flash-latest')
            response = await asyncio.to_thread(model.generate_content, gemini_input_text)
            gemini_output.update(response.text)
            self.call_after_refresh(self.query_one("#gemini-output-scrollable").focus)
        except Exception as e:
            gemini_output.update(f"Error: {e}")
            self.log(f"Gemini Error: {e}")

    def action_focus_next(self) -> None:
        self.screen.focus_next()

    def action_focus_previous(self) -> None:
        self.screen.focus_previous()

    async def action_forget_api_key(self, update_footer: bool = True) -> None:
        """Forgets the API key and disables Gemini features in the UI."""
        if os.path.exists(API_KEY_FILE):
            os.remove(API_KEY_FILE)
        
        genai.configure(api_key=None)
        
        gemini_input = self.query_one("#gemini-input", GeminiInput)
        gemini_input.load_text("API key forgotten. Press Ctrl+E to enter a new one.")
        gemini_input.disabled = True
        
        self.query_one("#gemini-output", Markdown).update("Gemini features disabled.")
        
        if update_footer:
            self.query_one(Footer).message = "API Key forgotten. Press Ctrl+E to re-enable."

    def action_enter_api_key(self) -> None:
        """Kicks off the flow to enter a new API key in a worker."""
        self.run_worker(self._run_enter_api_key_flow, exclusive=True)

    async def _run_enter_api_key_flow(self, is_first_run: bool = False) -> None:
        """Prompts for a new API key and enables Gemini features."""
        api_key = await self.push_screen_wait(APIKeyInputScreen())
        if api_key:
            with open(API_KEY_FILE, "w") as f:
                f.write(api_key)
            genai.configure(api_key=api_key)
            
            gemini_input = self.query_one("#gemini-input", GeminiInput)
            gemini_input.disabled = False
            gemini_input.load_text("")
            
            self.query_one("#gemini-output", Markdown).update("Gemini features enabled. Ask a question!")
            self.query_one(Footer).message = "Gemini API key saved and loaded."
        else:
            if is_first_run:
                 await self.action_forget_api_key(update_footer=False)
                 self.query_one(Footer).message = "API key not provided. Gemini features disabled. Press Ctrl+E to enter one."
            else:
                 self.query_one(Footer).message = "API key entry cancelled. Gemini features remain disabled."

if __name__ == "__main__":
    app = GeminiVimApp()
    app.run()