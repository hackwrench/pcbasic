"""
PC-BASIC - inputmethodss.py
Keyboard, pen and joystick handling

(c) 2013, 2014, 2015, 2016 Rob Hagemans
This file is released under the GNU GPL version 3 or later.
"""

import datetime
import logging

from . import error
from . import scancode
from . import values
from . import tokens as tk
from .eascii import as_bytes as ea
from .eascii import as_unicode as uea


# bit flags for modifier keys
toggle = {
    scancode.INSERT: 0x80, scancode.CAPSLOCK: 0x40,
    scancode.NUMLOCK: 0x20, scancode.SCROLLOCK: 0x10}
modifier = {
    scancode.ALT: 0x8, scancode.CTRL: 0x4,
    scancode.LSHIFT: 0x2, scancode.RSHIFT: 0x1}

# default function key eascii codes for KEY autotext.
function_key = {
    ea.F1: 0, ea.F2: 1, ea.F3: 2, ea.F4: 3,
    ea.F5: 4, ea.F6: 5, ea.F7: 6, ea.F8: 7,
    ea.F9: 8, ea.F10: 9, ea.F11: 10, ea.F12: 11}

# F12 emulator home-key
# also f12+b -> ctrl+break
home_key_replacements_scancode = {
    scancode.LEFT: (scancode.KP4, u'4'),
    scancode.RIGHT: (scancode.KP6, u'6'),
    scancode.UP: (scancode.KP8, u'8'),
    scancode.DOWN: (scancode.KP2, u'2'),
    # catch numbers by scancode, not eACSII
    # becasue the eASCII for Alt+number is different and that
    # will break inserting Alt+keypad numbers as Alt+F12+numbers
    scancode.N0: (scancode.KP0, u'0'),
    scancode.N1: (scancode.KP1, u'1'),
    scancode.N2: (scancode.KP2, u'2'),
    scancode.N3: (scancode.KP3, u'3'),
    scancode.N4: (scancode.KP4, u'4'),
    scancode.N5: (scancode.KP5, u'5'),
    scancode.N6: (scancode.KP6, u'6'),
    scancode.N7: (scancode.KP7, u'7'),
    scancode.N8: (scancode.KP8, u'8'),
    scancode.N9: (scancode.KP9, u'9'),
}

home_key_replacements_eascii = {
    u'+': (scancode.KPPLUS, u'+'),
    u'-': (scancode.KPMINUS, u'-'),
    u'P': (scancode.BREAK, u''),
    u'N': (scancode.NUMLOCK, u''),
    u'S': (scancode.SCROLLOCK, u''),
    u'C': (scancode.CAPSLOCK, u''),
}


###############################################################################
# keyboard queue

class KeyboardBuffer(object):
    """Quirky emulated ring buffer for keystrokes."""


    def __init__(self, sound, ring_length, fkey_macros):
        """Initialise to given length."""
        # buffer holds tuples (eascii/codepage, scancode, modifier)
        self.buffer = []
        self.ring_length = ring_length
        self.start = 0
        # expansion buffer for keyboard macros; also used for DBCS
        # expansion vessel holds codepage chars
        self.expansion_vessel = []
        self.sound = sound
        # function-key macros
        self.fkey_macros = fkey_macros

    def length(self):
        """Return the number of keystrokes in the buffer."""
        return min(self.ring_length, len(self.buffer))

    def is_empty(self):
        """True if no keystrokes in buffer."""
        return len(self.buffer) == 0 and len(self.expansion_vessel) == 0

    def insert(self, cp_s, check_full=True):
        """Append a string of e-ascii/codepage as keystrokes. Does not trigger events (we have no scancodes)."""
        d = ''
        for c in cp_s:
            if d or c != '\0':
                self.insert_keypress(d+c, None, None, check_full)
                d = ''
            elif c == '\0':
                # eascii code is \0 plus one char
                d = c

    def insert_keypress(self, cp_c, scancode, modifier, check_full=True):
        """Append a single keystroke with eascii/codepage, scancode, modifier."""
        if cp_c:
            if check_full and len(self.buffer) >= self.ring_length:
                # emit a sound signal when buffer is full (and we care)
                self.sound.play_sound_no_wait(800, 0.01)
            else:
                self.buffer.append((cp_c, scancode, modifier))

    def getc(self, expand=True):
        """Read a keystroke as eascii/codepage."""
        try:
            return self.expansion_vessel.pop(0)
        except IndexError:
            pass
        try:
            c = self.buffer.pop(0)[0]
        except IndexError:
            c = ''
        if c:
            self.start = (self.start + 1) % self.ring_length
        if not expand or c not in function_key:
            return c
        self.expansion_vessel = list(self.fkey_macros.get(function_key[c]))
        try:
            return self.expansion_vessel.pop(0)
        except IndexError:
            # function macro has been redefined as empty: return scancode
            # e.g. KEY 1, "" enables catching F1 with INKEY$
            return c

    def peek(self):
        """Show top keystroke in keyboard buffer as eascii/codepage."""
        try:
            return self.buffer[0][0]
        except IndexError:
            return ''

    def stop(self):
        """Ring buffer stopping index."""
        return (self.start + self.length()) % self.ring_length

    def ring_index(self, index):
        """Get index for ring position."""
        index -= self.start
        if index < 0:
            index += self.ring_length + 1
        return index

    def ring_read(self, index):
        """Read character at position i in ring as eascii/codepage."""
        index = self.ring_index(index)
        if index == self.ring_length:
            # marker of buffer position
            return '\x0d', 0
        try:
            return self.buffer[index][0:2]
        except IndexError:
            return '\0\0', 0

    def ring_write(self, index, c, scan):
        """Write character at position i in ring as eascii/codepage."""
        index = self.ring_index(index)
        if index < self.ring_length:
            try:
                self.buffer[index] = (c, scan, None)
            except IndexError:
                pass

    def ring_set_boundaries(self, start, stop):
        """Set start and stop index."""
        length = (stop - start) % self.ring_length
        # rotate buffer to account for new start and stop
        start_index = self.ring_index(start)
        stop_index = self.ring_index(stop)
        self.buffer = self.buffer[start_index:] + self.buffer[:stop_index]
        self.buffer += [('\0\0', None, None)]*(length - len(self.buffer))
        self.start = start


###############################################################################
# keyboard operations

class Keyboard(object):
    """Keyboard handling."""

    def __init__(self, events, screen, fkey_macros, codepage, sound, keystring, ignore_caps, ctrl_c_is_break):
        """Initilise keyboard state."""
        # key queue (holds bytes)
        self.buf = KeyboardBuffer(sound, 15, fkey_macros)
        # pre-buffer for keystrokes to enable event handling (holds unicode)
        self.prebuf = []
        # INP(&H60) scancode
        self.last_scancode = 0
        # active status of caps, num, scroll, alt, ctrl, shift modifiers
        self.mod = 0
        # store for alt+keypad ascii insertion
        self.keypad_ascii = ''
        # PAUSE is inactive
        self.pause = False
        # F12 is inactive
        self.home_key_active = False
        # ignore caps lock, let OS handle it
        self.ignore_caps = ignore_caps
        # if true, treat Ctrl+C *exactly* like ctrl+break (unlike GW-BASIC)
        self.ctrl_c_is_break = ctrl_c_is_break
        # screen is needed only for print_screen()
        self.screen = screen
        # pre-inserted keystrings
        self.codepage = codepage
        self.buf.insert(self.codepage.str_from_unicode(keystring), check_full=False)
        # redirected input stream has closed
        self._input_closed = False
        # events is needed for wait() in wait_char()
        self.events = events

    def read_chars(self, num):
        """Read num keystrokes, blocking."""
        word = []
        for _ in range(num):
            word.append(self.get_char_block())
        return word

    def get_char(self, expand=True):
        """Read any keystroke, nonblocking."""
        # wait a tick to reduce CPU load in loops
        self.events.wait()
        return self.buf.getc(expand)

    def wait_char(self):
        """Wait for character, then return it but don't drop from queue."""
        while self.buf.is_empty() and not self._input_closed:
            self.events.wait()
        return self.buf.peek()

    def get_char_block(self):
        """Read any keystroke, blocking."""
        self.wait_char()
        return self.buf.getc()

    def insert_chars(self, us, check_full=True):
        """Insert eascii/unicode string into keyboard buffer."""
        self.pause = False
        self.buf.insert(self.codepage.str_from_unicode(us), check_full)

    def key_down(self, c, scan, mods, check_full=True):
        """Insert a key-down event by eascii/unicode, scancode and modifiers."""
        # emulator home-key (f12) replacements
        if self.home_key_active:
            if c.upper() == u'B':
                # f12+b -> ctrl+break
                self.prebuf.append((u'', scancode.BREAK, modifier[scancode.CTRL], check_full))
                return
            try:
                scan, c = home_key_replacements_scancode[scan]
            except KeyError:
                try:
                    scan, c = home_key_replacements_eascii[c.upper()]
                except KeyError:
                    pass
        # F12 emulator home key combinations
        if scan == scancode.F12:
            self.home_key_active = True
        # Pause key state
        self.pause = False
        if scan is not None:
            self.last_scancode = scan
        # update ephemeral modifier status at every keypress
        # mods is a list of scancodes; OR together the known modifiers
        self.mod &= ~(modifier[scancode.CTRL] | modifier[scancode.ALT] | modifier[scancode.LSHIFT] | modifier[scancode.RSHIFT])
        for m in mods:
            self.mod |= modifier.get(m, 0)
        # set toggle-key modifier status
        # these are triggered by keydown events
        try:
            self.mod ^= toggle[scan]
        except KeyError:
            pass
        # alt+keypad ascii replacement
        if (scancode.ALT in mods):
            try:
                self.keypad_ascii += scancode.keypad[scan]
                return
            except KeyError:
                pass
        if (self.mod & toggle[scancode.CAPSLOCK]
                and not self.ignore_caps and len(c) == 1):
            c = c.swapcase()
        self.prebuf.append((c, scan, self.mod, check_full))

    def key_up(self, scan):
        """Insert a key-up event."""
        if scan is not None:
            self.last_scancode = 0x80 + scan
        try:
            # switch off ephemeral modifiers
            self.mod &= ~modifier[scan]
        except KeyError:
           pass
        # ALT+keycode
        if scan == scancode.ALT and self.keypad_ascii:
            char = chr(int(self.keypad_ascii)%256)
            if char == '\0':
                char = '\0\0'
            self.buf.insert(char, check_full=True)
            self.keypad_ascii = ''
        elif scan == scancode.F12:
            self.home_key_active = False

    def close_input(self):
        """Signal that input stream has closed."""
        self._input_closed = True

    def drain_event_buffer(self):
        """Drain prebuffer into key buffer and handle trappable special keys."""
        while self.prebuf:
            c, scan, mod, check_full = self.prebuf.pop(0)
            # handle special key combinations
            if (scan == scancode.DELETE and
                    mod & (modifier[scancode.CTRL] | modifier[scancode.ALT])):
                # ctrl-alt-del: if not captured by the OS, reset the emulator
                # meaning exit and delete state. This is useful on android.
                raise error.Reset()
            elif ((scan in (scancode.BREAK, scancode.SCROLLOCK) and
                    mod & modifier[scancode.CTRL]) or
                    (self.ctrl_c_is_break and c == uea.CTRL_c)):
                raise error.Break()
            elif (scan == scancode.BREAK or
                    (scan == scancode.NUMLOCK and mod & modifier[scancode.CTRL])):
                self.pause = True
                return
            elif scan == scancode.PRINT:
                if mod & (modifier[scancode.LSHIFT] | modifier[scancode.RSHIFT]):
                    # shift + printscreen
                    self.screen.print_screen()
            self.buf.insert_keypress(
                    self.codepage.from_unicode(c),
                    scan, mod, check_full)


###############################################################################
# light pen

class Pen(object):
    """Light pen support."""

    def __init__(self, screen):
        """Initialise light pen."""
        self.is_down = False
        self.pos = 0, 0
        # signal pen has been down for PEN polls in pen_()
        self.was_down = False
        # signal pen has been down for event triggers in poll_event()
        self.was_down_event = False
        self.down_pos = (0, 0)
        self.screen = screen

    def down(self, x, y):
        """Report a pen-down event at graphical x,y """
        # TRUE until polled
        self.was_down = True
        # TRUE until events checked
        self.was_down_event = True
        # TRUE until pen up
        self.is_down = True
        self.down_pos = x, y

    def up(self):
        """Report a pen-up event at graphical x,y """
        self.is_down = False

    def moved(self, x, y):
        """Report a pen-move event at graphical x,y """
        self.pos = x, y

    def poll_event(self):
        """Poll the pen for a pen-down event since last poll."""
        result, self.was_down_event = self.was_down_event, False
        return result

    def poll(self, fn, enabled):
        """PEN: poll the light pen."""
        fn = values.to_int(fn)
        error.range_check(0, 9, fn)
        posx, posy = self.pos
        fw = self.screen.mode.font_width
        fh = self.screen.mode.font_height
        if fn == 0:
            pen_down_old, self.was_down = self.was_down, False
            pen = -1 if pen_down_old else 0
        elif fn == 1:
            pen = self.down_pos[0]
        elif fn == 2:
            pen = self.down_pos[1]
        elif fn == 3:
            pen = -1 if self.is_down else 0
        elif fn == 4:
            pen = posx
        elif fn == 5:
            pen = posy
        elif fn == 6:
            pen = 1 + self.down_pos[1]//fh
        elif fn == 7:
            pen = 1 + self.down_pos[0]//fw
        elif fn == 8:
            pen = 1 + posy//fh
        elif fn == 9:
            pen = 1 + posx//fw
        if not enabled:
            # should return 0 or char pos 1 if PEN not ON
            pen = 1 if fn >= 6 else 0
        return pen


###############################################################################
# joysticks


class Stick(object):
    """Joystick support."""

    def __init__(self):
        """Initialise joysticks."""
        self.is_firing = [[False, False], [False, False]]
        # axis 0--255; 128 is mid but reports 0, not 128 if no joysticks present
        self.axis = [[0, 0], [0, 0]]
        self.is_on = False
        self.was_fired = [[False, False], [False, False]]
        self.was_fired_event = [[False, False], [False, False]]
        # timer for reading game port
        self.out_time = self._decay_timer()

    def strig_statement_(self, args):
        """Switch joystick handling on or off."""
        on, = args
        self.is_on = (on == tk.ON)

    def down(self, joy, button):
        """Report a joystick button down event."""
        try:
            self.was_fired[joy][button] = True
            self.is_firing[joy][button] = True
            self.was_fired_event[joy][button] = True
        except IndexError:
            # ignore any joysticks/axes beyond the 2x2 supported by BASIC
            pass

    def up(self, joy, button):
        """Report a joystick button up event."""
        try:
            self.is_firing[joy][button] = False
        except IndexError:
            # ignore any joysticks/axes beyond the 2x2 supported by BASIC
            pass

    def moved(self, joy, axis, value):
        """Report a joystick axis move."""
        try:
            self.axis[joy][axis] = value
        except IndexError:
            # ignore any joysticks/axes beyond the 2x2 supported by BASIC
            pass

    def poll_event(self, joy, button):
        """Poll the joystick for button events since last poll."""
        result = self.was_fired_event[joy][button]
        self.was_fired_event[joy][button] = False
        return result

    def stick_(self, fn):
        """STICK: poll the joystick axes."""
        fn = values.to_int(fn)
        error.range_check(0, 3, fn)
        joy, axis = fn // 2, fn % 2
        try:
            return self.axis[joy][axis]
        except IndexError:
            # ignore any joysticks/axes beyond the 2x2 supported by BASIC
            pass

    def strig_(self, fn):
        """STRIG: poll the joystick fire button."""
        fn = values.to_int(fn)
        error.range_check(0, 7, fn)
        # 0,1 -> [0][0] 2,3 -> [0][1]  4,5-> [1][0]  6,7 -> [1][1]
        joy, trig = fn // 4, (fn//2) % 2
        if fn % 2 == 0:
            # has been fired
            stick_was_trig = self.was_fired[joy][trig]
            self.was_fired[joy][trig] = False
            return -1 if stick_was_trig else 0
        else:
            # is currently firing
            return -1 if self.is_firing[joy][trig] else 0

    def decay(self):
        """Return time since last game port reset."""
        return (self._decay_timer() - self.out_time) % 86400000

    def reset_decay(self):
        """Reset game port."""
        self.out_time = self._decay_timer()

    def _decay_timer(self):
        """Millisecond timer for game port decay."""
        now = datetime.datetime.now()
        return now.second*1000 + now.microsecond/1000
