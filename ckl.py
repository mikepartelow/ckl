#!/usr/bin/env python3
import os
import logging
import json
import argparse
from prompt_toolkit import Application
from prompt_toolkit.layout.containers import HSplit, Window, FloatContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.screen import Point
from prompt_toolkit.layout import FloatContainer, Float
from prompt_toolkit.widgets import Dialog, Label, Button
from prompt_toolkit.formatted_text import ANSI
from prompt_toolkit.styles import Style
from prompt_toolkit.layout.margins import NumberedMargin, ScrollbarMargin
from prompt_toolkit.shortcuts import message_dialog


LISTS_ROOT      = 'lists'
SESSIONS_ROOT   = 'sessions'

class DuplicatesWarningDialog(Dialog):
    def __init__(self, duplicates):
        def all_done_ok_button_handler():
            root_container.floats.pop()
            app.layout.focus(checklist_window)

        text = []
        it = iter(session.duplicates)
        for dup in it:
            for d in (dup, next(it)):
                logging.warning("duplicate item: %s in %s line %s", *d)
                text.append("{}: from {} line {}".format(*d))
            text.append('')

        super().__init__(title=ANSI('Warning: Duplicate Items'), body=Window(content=FormattedTextControl(ANSI("\n".join(text)))),
                         buttons=[Button(text='OK', handler=all_done_ok_button_handler),],)

class OKDialog:
    def __init__(self, title="Epic", body="OK"):
        def all_done_ok_button_handler():
            root_container.floats.pop()
            app.layout.focus(checklist_window)

        width = app.layout.current_window.render_info.window_width - 8

        dialog = Dialog(title=title, body=Window(content=FormattedTextControl(text=ANSI(body.strip()))),
                        buttons=[Button(text='OK', handler=all_done_ok_button_handler),],
                        width=width)

        root_container.floats.append(Float(content=dialog))
        app.layout.focus(root_container)

class HelpDialog(OKDialog):
    def __init__(self):
        super().__init__(title='Help', body="""
q         : quit
<UP>      : move cursor up
<DOWN>    : move cursor down
<SPACE>   : check/uncheck item
c         : show/hide checked items
z         : undo
R         : reset checklist
<CTRL>-F  : page down
<CTRL>-B  : page up
<CTRL>-Z  : go to end
<CTRL>-A  : go to beginning""")

class ChecklistCompletedDialog():
    def __init__(self, checklist_control):
        def all_done_ok_button_handler():
            root_container.floats.pop()
            app.layout.focus(checklist_window)
            checklist_control.adjust_cursor_position()

        self.dialog = Dialog(title='Checklist Completed',
                             body=Label(text='      Fine Work'),
                             buttons=[Button(text='Indeed', handler=all_done_ok_button_handler),])

        root_container.floats.append(Float(content=self.dialog))
        app.layout.focus(root_container)

class ResetConfirmationDialog():
    def __init__(self, checklist_control):
        def handler():
            root_container.floats.pop()
            app.layout.focus(checklist_window)
            checklist_control.adjust_cursor_position()

        def yes_handler():
            checklist_control.undo_stack = []
            [ i.uncheck() for i in checklist_control.checklist.items() ]
            checklist_control.session.dump()

            handler()

        def no_handler():
            handler()

        self.dialog = Dialog(title='Uncheck All?',
                             body=Label(text='This will erase all undo history.'),
                             buttons=[Button(text='No', handler=no_handler),
                                      Button(text='Yes', handler=yes_handler)])

        root_container.floats.append(Float(content=self.dialog))
        app.layout.focus(root_container)



class Item:
    def __init__(self, name, checked=False):
        self.name       = name.strip()
        self.checked    = checked

    @property
    def unchecked(self):
        return not self.checked

    def __str__(self):
        return self.name

    def check(self, checked=True):
        self.checked = checked

    def uncheck(self):
        self.check(checked=False)

    def toggle(self):
        self.check(not self.checked)

class Checklist(Item):
    def __init__(self, name, items, checked=False):
        super().__init__(name, checked)
        self._items         = items

    def check(self, checked=True):
        super().check(checked)
        for item in self._items:
            item.check(checked)

    def items(self, level=0, with_level=False):
        the_items = []

        for item in self._items:
            if with_level:
                the_items.append((item, level))
            else:
                the_items.append(item)
            if hasattr(item, 'items'):
                the_items.extend(item.items(level=level+1, with_level=with_level))

        return the_items

    def merge(self, other):
        for other_item in other._items:
            our_item = next((i for i in self._items if i.name==other_item.name), None)
            if our_item is not None:
                if hasattr(other_item, 'items'):
                    our_item.merge(other_item)
            else:
                self._items.append(other_item)

        return self

class ChecklistParser:
    def __init__(self, path, name=None, items={}):
        self.items = items
        self.path = path
        self.name = name or os.path.basename(self.path).replace('.ckl', '')
        self.checklist = Checklist(self.name, [])

    def add_item(self, item, linenum):
        self.checklist._items.append(item)
        self.items.setdefault(item.name, []).append((item, self.path, linenum))

    def pop_item(self):
        item = self.checklist._items.pop()
        self.items[item.name] = [ i for i in self.items[item.name] if i[0] != item ]
        return item

    def dups(self):
        dups = []

        for name, itemlist in self.items.items():
            if len(itemlist) > 1:
                dups.extend(itemlist)

        return dups

    def load(self):
        with open(self.path, 'r') as f:
            return self.loads(f.readlines())

    def loads(self, lines, indent=0, linenum_=0):
        parents = []
        linenum = linenum_

        while len(lines) > 0:
            line = lines.pop(0)
            linenum += 1

            logging.debug(" file: %s linenum: %s, line: [%s]", os.path.basename(self.path), linenum, line.strip())

            if line.startswith('#'):
                pass
            elif line.startswith('from:'):
                parent_name = line.split('from:')[1].strip()
                path_to_parent = os.path.join(LISTS_ROOT, parent_name) + '.ckl'
                parents.append(ChecklistParser(path_to_parent, items=self.items).load().merge(self.checklist))
            else:
                new_indent = len(line) - len(line.lstrip(' '))

                if new_indent == indent:
                    self.add_item(Item(line), linenum)
                else:
                    lines.insert(0, line)

                    if new_indent > indent:
                        # this is a "sublist". so, remove the Item and replace it with a Checklist
                        #
                        line_count_before = len(lines)
                        sublist = ChecklistParser(self.path, name=self.pop_item().name).loads(lines, indent=new_indent, linenum_=linenum-1)
                        self.checklist._items.append(sublist)
                        linenum += line_count_before - len(lines) - 1
                    else:
                        break

        if parents:
            parents.append(self.checklist)
            for p in parents[1:]:
                parents[0].merge(p)

            self.checklist = parents[0]
            self.checklist.name = self.name

        return self.checklist

class ChecklistSession:
    def __init__(self, path_to_checklist):
        self.path_to_checklist = path_to_checklist
        self.path_to_session = path_to_checklist.replace(LISTS_ROOT, SESSIONS_ROOT)
        self.checklist = None
        self.duplicates = None

    def load(self):
        if os.path.exists(self.path_to_session):
            with open(self.path_to_session, 'r') as f:
                self.checklist = ChecklistSession.loads(f.read())
        else:
            parser = ChecklistParser(self.path_to_checklist)
            self.checklist = parser.load()
            self.duplicates = parser.dups()

        return self.checklist

    def dump(self):
        os.makedirs(os.path.dirname(self.path_to_session), exist_ok=True)
        with open(self.path_to_session, 'w') as f:
            f.write(ChecklistSession.dumps(self.checklist))

    @staticmethod
    def dumps(checklist):
        def to_dict(ckl):
            items = []

            for i in ckl._items:
                if hasattr(i, 'items'):
                    d = dict(name=i.name, checked=i.checked)
                    d['items'] = to_dict(i)
                else:
                    d = dict(name=i.name, checked=i.checked)

                items.append(d)
            return items

        return json.dumps(dict(name=checklist.name, items=to_dict(checklist), checked=checklist.checked))

    @staticmethod
    def loads(str):
        def from_dict(d):
            if 'items' in d:
                i = Checklist(name=d['name'], items=[ from_dict(d) for d in d['items'] ], checked=d['checked'])
            else:
                i = Item(name=d['name'], checked=d['checked'])

            return i

        return from_dict(json.loads(str))

class ChecklistControl(FormattedTextControl):
    def __init__(self, checklist, session):
        super().__init__(text=self.checklist_text, get_cursor_position=lambda: self.cursor_position, key_bindings=self.build_key_bindings())

        self.checklist = checklist
        self.session = session
        self.cursor_position = Point(1,0)
        self.undo_stack = []
        self.show_completed = False

    def undo_push(self, item):
        if hasattr(item, 'items'):
            self.undo_stack.append((item, [i.checked for i in item.items()]))
        self.undo_stack.append((item, item.checked))

    def undo_pop(self):
        item, checked = self.undo_stack.pop()
        item.check(checked=checked)

        if hasattr(item, 'items'):
            item, checked = self.undo_stack.pop()

            for i, checked in zip(item.items(), checked):
                i.check(checked=checked)

        return item

    def displayed_items(self):
        if self.show_completed:
            return self.checklist.items()
        else:
            return [i for i in self.checklist.items() if i.unchecked]

    def adjust_cursor_position(self, x=None, y=None):
        d_items = self.displayed_items()
        if y is not None:
            if y < len(d_items):
                self.cursor_position = Point(1, y)
        if len(d_items) == 0:
            self.cursor_position = Point(1,0)
        elif self.cursor_position.y >= len(d_items):
            self.cursor_position = Point(1, len(d_items)-1)

    def checklist_text(self):
        s = []
        idx = 0
        for (item, level) in self.checklist.items(with_level=True):
            if item.unchecked or self.show_completed:
                if idx == self.cursor_position.y:
                    ansi0, ansi1 = '\x1b[7m', '\x1b[0m'
                else:
                    ansi0, ansi1 = '', ''

                if hasattr(item, 'items'):
                    ansi0, ansi1 = '\x1b[1m', '\x1b[0m'
                else:
                    ansi2, ansi3 = '', ''

                idx += 1

                s.append("{ansi0}[{checked}] {indent}{name}{ansi1}".format(ansi0=ansi0, ansi1=ansi1, checked='x' if item.checked else ' ', indent=' '*2*level, name=item.name))

        if len(s) > 0:
            return ANSI("\n".join(s))
        else:
            return '[🥇]'

    def build_key_bindings(self):
        kb = KeyBindings()

        @kb.add('c')
        def _(event):
            self.show_completed = not self.show_completed
            self.adjust_cursor_position()

        @kb.add('z')
        def _(event):
            if len(self.undo_stack) > 0:
                item = self.undo_pop()
                self.session.dump()
                try:
                    self.adjust_cursor_position(y=self.displayed_items().index(item))
                except ValueError:
                    self.adjust_cursor_position()

        @kb.add('R')
        def _(event):
            ResetConfirmationDialog(self)

        @kb.add('down')
        def _(event):
            new_y = self.cursor_position.y + 1

            if new_y < len(self.displayed_items()):
                self.adjust_cursor_position(y=new_y)

        @kb.add('up')
        def _(event):
            new_y = self.cursor_position.y - 1
            if new_y >= 0:
                self.adjust_cursor_position(y=new_y)

        @kb.add('c-f')
        def _(event):
            w = event.app.layout.current_window

            if w and w.render_info:
                line_index = max(w.render_info.last_visible_line(), w.vertical_scroll + 1)
                self.adjust_cursor_position(y=line_index)
                w.vertical_scroll = line_index

        @kb.add('c-b')
        def _(event):
            w = event.app.layout.current_window

            if w and w.render_info:
                line_index = max(0, self.cursor_position.y-w.render_info.window_height+1)
                self.adjust_cursor_position(y=line_index)

        @kb.add('c-a')
        def _(event):
            self.adjust_cursor_position(y=0)

        @kb.add('c-z')
        def _(event):
            w = event.app.layout.current_window

            if w and w.render_info:
                line_index = len(self.displayed_items())-1
                self.adjust_cursor_position(y=line_index)
                w.vertical_scroll = line_index

        @kb.add('space')
        def _(event):
            d_items = self.displayed_items()
            if len(d_items) > 0:
                item = d_items[self.cursor_position.y]
                self.undo_push(item)
                item.toggle()
                self.session.dump()

                self.adjust_cursor_position()

                if len([i for i in self.checklist.items() if i.unchecked]) == 0:
                    ChecklistCompletedDialog(self)

        return kb

if __name__ == "__main__":
    logging.basicConfig(filename='ckl.log', level=logging.DEBUG)

    parser = argparse.ArgumentParser()
    parser.add_argument("path_to_checklist", help="path to checklist")
    args = parser.parse_args()

    # args = type('',(object,),{'path_to_checklist':"{}/travel/full.ckl".format(LISTS_ROOT)})()
    # args = type('',(object,),{'path_to_checklist':"{}/travel/full.ckl.session.0".format(SESSIONS_ROOT)})()

    session = ChecklistSession(args.path_to_checklist)
    checklist = session.load()

    def status_bar_text():
        items = checklist.items()
        return "{name} : {checked}/{total} done | 'q': quit | 'z': undo | '?' help | <up>/<down> moves | <space> toggles".format(
                name = checklist.name,
                checked=len([i for i in items if i.checked]),
                total=len(items))

    checklist_window = Window(ChecklistControl(checklist, session), left_margins=[NumberedMargin()], right_margins=[ScrollbarMargin(display_arrows=True),])
    status_bar_window = Window(content=FormattedTextControl(status_bar_text), height=1, style='reverse')

    root_container = FloatContainer(
        content=HSplit([
            checklist_window,
            status_bar_window,
        ]),
        floats=[
        ],
    )

    if session.duplicates:
        root_container.floats.append(Float(content=DuplicatesWarningDialog(session.duplicates)))

    kb = KeyBindings()

    @kb.add('q')
    def _(event):
        event.app.exit()

    @kb.add('?')
    @kb.add('h')
    def _(event):
        HelpDialog()

    app = Application(layout=Layout(root_container), full_screen=True, key_bindings=kb)
    app.run()