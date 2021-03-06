"""
PC-BASIC - statements.py
Statement parser

(c) 2013, 2014, 2015, 2016 Rob Hagemans
This file is released under the GNU GPL version 3 or later.
"""

import os
import logging
import string
import struct
import io
from functools import partial

from . import error
from . import values
from . import ports
from . import tokens as tk
from . import dos


class StatementParser(object):
    """BASIC statements."""

    def __init__(self, values, temp_string, memory, expression_parser, syntax):
        """Initialise statement context."""
        # syntax: advanced, pcjr, tandy
        self.syntax = syntax
        self.values = values
        self.expression_parser = expression_parser
        # temporary string context guard
        self.temp_string = temp_string
        # data segment
        self.memory = memory

    def parse_statement(self, ins):
        """Parse and execute a single statement."""
        # read keyword token or one byte
        ins.skip_blank()
        c = ins.read_keyword_token()
        if c in self._simple:
            # statement token
            args_iter = self._simple[c]
        elif c in self._complex:
            stat_dict = self._complex[c]
            selector = ins.skip_blank()
            if selector not in stat_dict.keys():
                selector = None
            else:
                c += selector
            # statement token
            args_iter = stat_dict[selector]
        elif c == '_':
            # extension statement
            word = ins.read_name()
            try:
                args_iter = self._extensions[word]
                c += word
            except KeyError:
                raise error.RunError(error.STX)
        else:
            # implicit LET
            ins.seek(-len(c), 1)
            if c in string.ascii_letters:
                c = tk.LET
                args_iter = self._simple[tk.LET]
            else:
                ins.require_end()
                return
        self._callbacks[c](args_iter(ins))
        if c != tk.IF:
            ins.require_end()

    def parse_name(self, ins):
        """Get scalar part of variable name from token stream."""
        name = ins.read_name()
        # must not be empty
        error.throw_if(not name, error.STX)
        # append sigil, if missing
        return self.memory.complete_name(name)

    def parse_expression(self, ins, allow_empty=False):
        """Compute the value of the expression at the current code pointer."""
        if allow_empty and ins.skip_blank() in tk.END_EXPRESSION:
            return None
        self.session.redo_on_break = True
        val = self.expression_parser.parse(ins)
        self.session.redo_on_break = False
        return val

    def _parse_value(self, ins, sigil=None, allow_empty=False):
        """Read a value of required type and return as Python value, or None if empty."""
        expr = self.parse_expression(ins, allow_empty)
        if expr is not None:
            # this will force into the requested type; e.g. Integers may overflow
            return values.to_type(sigil, expr).to_value()
        return None

    def _parse_bracket(self, ins):
        """Compute the value of the bracketed expression."""
        ins.require_read(('(',))
        # we'll get a Syntax error, not a Missing operand, if we close with )
        val = self.parse_expression(ins)
        ins.require_read((')',))
        return val

    def _parse_temporary_string(self, ins, allow_empty=False):
        """Parse an expression and return as Python value. Store strings in a temporary."""
        # if allow_empty, a missing value is returned as an empty string
        with self.temp_string:
            expr = self.parse_expression(ins, allow_empty)
            if expr:
                return values.pass_string(expr).to_value()
            return None

    def _parse_file_number(self, ins, opt_hash):
        """Read a file number."""
        if not ins.skip_blank_read_if(('#',)) and not opt_hash:
            return None
        number = values.to_int(self.parse_expression(ins))
        error.range_check(0, 255, number)
        return number

    def _parse_variable(self, ins):
        """Helper function: parse a scalar or array element."""
        name = ins.read_name()
        error.throw_if(not name, error.STX)
        # this is an evaluation-time determination
        # as we could have passed another DEFtype statement
        name = self.memory.complete_name(name)
        self.session.redo_on_break = True
        indices = self.expression_parser.parse_indices(ins)
        self.session.redo_on_break = False
        return name, indices

    def _parse_jumpnum(self, ins):
        """Parses a line number pointer as in GOTO, GOSUB, LIST, RENUM, EDIT, etc."""
        ins.require_read((tk.T_UINT,))
        token = ins.read(2)
        assert len(token) == 2, 'Bytecode truncated in line number pointer'
        return struct.unpack('<H', token)[0]

    def _parse_optional_jumpnum(self, ins):
        """Parses a line number pointer as in GOTO, GOSUB, LIST, RENUM, EDIT, etc."""
        # no line number
        if ins.skip_blank() != tk.T_UINT:
            return None
        return self._parse_jumpnum(ins)

    ###########################################################################

    def init_statements(self, session):
        """Initialise statements."""
        self.session = session
        self._simple = {
            tk.DATA: self._skip_statement,
            tk.REM: self._skip_line,
            tk.ELSE: self._skip_line,
            tk.CONT: self._parse_nothing,
            tk.TRON: self._parse_nothing,
            tk.TROFF: self._parse_nothing,
            tk.WHILE: self._parse_nothing,
            tk.RESET: self._parse_end,
            tk.END: self._parse_end,
            tk.STOP: self._parse_end,
            tk.NEW: self._parse_end,
            tk.WEND: self._parse_end,
            tk.SYSTEM: self._parse_end,
            tk.FOR: self._parse_for_args_iter,
            tk.NEXT: self._parse_next_args_iter,
            tk.INPUT: self._parse_input_args_iter,
            tk.DIM: self._parse_var_list_iter,
            tk.READ: self._parse_var_list_iter,
            tk.LET: self._parse_let_args_iter,
            tk.GOTO: self._parse_single_line_number_iter,
            tk.RUN: self._parse_run_args_iter,
            tk.IF: self._parse_if_args_iter,
            tk.RESTORE: self._parse_restore_args_iter,
            tk.GOSUB: self._parse_single_line_number_iter,
            tk.RETURN: self._parse_optional_line_number_iter,
            tk.PRINT: partial(self._parse_print_args_iter, parse_file=True),
            tk.CLEAR: self._parse_clear_args_iter,
            tk.LIST: self._parse_list_args_iter,
            tk.WAIT: self._parse_wait_args_iter,
            tk.POKE: self._parse_poke_out_args_iter,
            tk.OUT: self._parse_poke_out_args_iter,
            tk.LPRINT: partial(self._parse_print_args_iter, parse_file=False),
            tk.LLIST: self._parse_delete_llist_args_iter,
            tk.WIDTH: self._parse_width_args_iter,
            tk.SWAP: self._parse_swap_args_iter,
            tk.ERASE: self._parse_erase_args_iter,
            tk.EDIT: self._parse_edit_args_iter,
            tk.ERROR: self._parse_single_arg_iter,
            tk.RESUME: self._parse_resume_args_iter,
            tk.DELETE: self._parse_delete_llist_args_iter,
            tk.AUTO: self._parse_auto_args_iter,
            tk.RENUM: self._parse_renum_args_iter,
            tk.DEFSTR: self._parse_deftype_args_iter,
            tk.DEFINT: self._parse_deftype_args_iter,
            tk.DEFSNG: self._parse_deftype_args_iter,
            tk.DEFDBL: self._parse_deftype_args_iter,
            tk.CALL: self._parse_call_args_iter,
            tk.CALLS: self._parse_call_args_iter,
            tk.WRITE: self._parse_write_args_iter,
            tk.OPTION: self._parse_option_base_args_iter,
            tk.RANDOMIZE: self._parse_optional_arg_iter,
            tk.OPEN: self._parse_open_args_iter,
            tk.CLOSE: self._parse_close_args_iter,
            tk.LOAD: self._parse_load_args_iter,
            tk.MERGE: self._parse_single_string_arg_iter,
            tk.SAVE: self._parse_save_args_iter,
            tk.COLOR: self._parse_color_args_iter,
            tk.CLS: self._parse_cls_args_iter,
            tk.MOTOR: self._parse_optional_arg_iter,
            tk.BSAVE: self._parse_bsave_args_iter,
            tk.BLOAD: self._parse_bload_args_iter,
            tk.SOUND: self._parse_sound_args_iter,
            tk.BEEP: self._parse_beep_args_iter,
            tk.PSET: self._parse_pset_preset_args_iter,
            tk.PRESET: self._parse_pset_preset_args_iter,
            tk.SCREEN: self._parse_screen_args_iter,
            tk.LOCATE: self._parse_locate_args_iter,
            tk.FILES: self._parse_optional_string_arg_iter,
            tk.FIELD: self._parse_field_args_iter,
            tk.NAME: self._parse_name_args_iter,
            tk.LSET: self._parse_let_args_iter,
            tk.RSET: self._parse_let_args_iter,
            tk.KILL: self._parse_single_string_arg_iter,
            tk.COMMON: self._parse_common_args_iter,
            tk.CHAIN: self._parse_chain_args_iter,
            tk.DATE: self._parse_time_date_args_iter,
            tk.TIME: self._parse_time_date_args_iter,
            tk.PAINT: self._parse_paint_args_iter,
            tk.COM: self._parse_com_command_iter,
            tk.CIRCLE: self._parse_circle_args_iter,
            tk.DRAW: self._parse_string_arg_iter,
            tk.TIMER: self._parse_event_command_iter,
            tk.IOCTL: self._parse_ioctl_args_iter,
            tk.CHDIR: self._parse_single_string_arg_iter,
            tk.MKDIR: self._parse_single_string_arg_iter,
            tk.RMDIR: self._parse_single_string_arg_iter,
            tk.SHELL: self._parse_optional_string_arg_iter,
            tk.ENVIRON: self._parse_single_string_arg_iter,
            tk.WINDOW: self._parse_window_args_iter,
            tk.LCOPY: self._parse_optional_arg_iter,
            tk.PCOPY: self._parse_pcopy_args_iter,
            tk.LOCK: self._parse_lock_unlock_args_iter,
            tk.UNLOCK: self._parse_lock_unlock_args_iter,
            tk.MID: self._parse_mid_args_iter,
            tk.PEN: self._parse_event_command_iter,
        }
        if self.syntax in ('pcjr', 'tandy'):
            self._simple.update({
                tk.TERM: self._parse_end,
                tk.NOISE: self._parse_noise_args_iter,
            })
        self._complex = {
            tk.ON: {
                tk.ERROR: self._parse_on_error_goto_args_iter,
                tk.KEY: self._parse_on_event_args_iter,
                '\xFE': self._parse_on_event_args_iter,
                '\xFF': self._parse_on_event_args_iter,
                None: self._parse_on_jump_args_iter,
            },
            tk.DEF: {
                tk.FN: self._parse_def_fn_args_iter,
                tk.USR: self._parse_def_usr_args_iter,
                None: self._parse_def_seg_args_iter,
            },
            tk.LINE: {
                tk.INPUT: self._parse_line_input_args_iter,
                None: self._parse_line_args_iter,
            },
            tk.KEY: {
                tk.ON: self._parse_key_macro_args_iter,
                tk.OFF: self._parse_key_macro_args_iter,
                tk.LIST: self._parse_key_macro_args_iter,
                '(': self._parse_com_command_iter,
                None: self._parse_key_define_args_iter,
            },
            tk.PUT: {
                '(': self._parse_put_graph_args_iter,
                None: self._parse_put_get_file_args_iter,
            },
            tk.GET: {
                '(': self._parse_get_graph_args_iter,
                None: self._parse_put_get_file_args_iter,
            },
            tk.PLAY: {
                tk.ON: self._parse_event_command_iter,
                tk.OFF: self._parse_event_command_iter,
                tk.STOP: self._parse_event_command_iter,
                None: self._parse_play_args_iter,
            },
            tk.VIEW: {
                tk.PRINT: self._parse_view_print_args_iter,
                None: self._parse_view_args_iter,
            },
            tk.PALETTE: {
                tk.USING: self._parse_palette_using_args_iter,
                None: self._parse_palette_args_iter,
            },
            tk.STRIG: {
                tk.ON: self._parse_strig_switch_iter,
                tk.OFF: self._parse_strig_switch_iter,
                None: self._parse_com_command_iter,
            },
        }
        self._extensions = {
            'DEBUG': self._parse_single_string_arg_iter,
        }
        self._callbacks = {
            tk.DATA: list,
            tk.REM: list,
            tk.ELSE: list,
            tk.CONT: session.interpreter.cont_,
            tk.TRON: session.interpreter.tron_,
            tk.TROFF: session.interpreter.troff_,
            tk.WHILE: session.interpreter.while_,
            tk.RESET: session.files.reset_,
            tk.END: session.end_,
            tk.STOP: session.interpreter.stop_,
            tk.NEW: session.new_,
            tk.WEND: session.interpreter.wend_,
            tk.SYSTEM: session.interpreter.system_,
            tk.FOR: session.interpreter.for_,
            tk.NEXT: session.interpreter.next_,
            tk.INPUT: session.input_,
            tk.DIM: session.memory.arrays.dim_,
            tk.READ: session.interpreter.read_,
            tk.LET: session.memory.let_,
            tk.GOTO: session.interpreter.goto_,
            tk.RUN: session.run_,
            tk.IF: session.interpreter.if_,
            tk.RESTORE: session.interpreter.restore_,
            tk.GOSUB: session.interpreter.gosub_,
            tk.RETURN: session.interpreter.return_,
            tk.PRINT: session.files.print_,
            tk.CLEAR: session.clear_,
            tk.LIST: session.list_,
            tk.WAIT: session.machine.wait_,
            tk.POKE: session.all_memory.poke_,
            tk.OUT: session.machine.out_,
            tk.LPRINT: session.devices.lprint_,
            tk.LLIST: session.llist_,
            tk.WIDTH: session.files.width_,
            tk.SWAP: session.memory.swap_,
            tk.ERASE: session.memory.arrays.erase_,
            tk.EDIT: session.edit_,
            tk.ERROR: session.error_,
            tk.RESUME: session.interpreter.resume_,
            tk.DELETE: session.delete_,
            tk.AUTO: session.auto_,
            tk.RENUM: session.renum_,
            tk.DEFSTR: session.memory.defstr_,
            tk.DEFINT: session.memory.defint_,
            tk.DEFSNG: session.memory.defsng_,
            tk.DEFDBL: session.memory.defdbl_,
            tk.CALL: session.all_memory.call_,
            tk.CALLS: session.all_memory.call_,
            tk.WRITE: session.files.write_,
            tk.OPTION: session.memory.arrays.option_base_,
            tk.RANDOMIZE: session.randomize_,
            tk.OPEN: session.files.open_,
            tk.CLOSE: session.files.close_,
            tk.LOAD: session.load_,
            tk.MERGE: session.merge_,
            tk.SAVE: session.save_,
            tk.COLOR: session.screen.color_,
            tk.CLS: session.screen.cls_,
            tk.MOTOR: session.devices.motor_,
            tk.BSAVE: session.all_memory.bsave_,
            tk.BLOAD: session.all_memory.bload_,
            tk.SOUND: session.sound.sound_,
            tk.BEEP: session.sound.beep_,
            tk.PSET: session.screen.drawing.pset_,
            tk.PRESET: session.screen.drawing.preset_,
            tk.SCREEN: session.screen.screen_,
            tk.LOCATE: session.screen.locate_,
            tk.FILES: session.devices.files_,
            tk.FIELD: session.files.field_,
            tk.NAME: session.devices.name_,
            tk.LSET: session.memory.lset_,
            tk.RSET: session.memory.rset_,
            tk.KILL: session.devices.kill_,
            tk.COMMON: session.common_,
            tk.CHAIN: session.chain_,
            tk.DATE: session.clock.date_,
            tk.TIME: session.clock.time_,
            tk.PAINT: partial(session.screen.drawing.paint_, events=session.events),
            tk.COM: session.events.com_,
            tk.CIRCLE: session.screen.drawing.circle_,
            tk.DRAW: partial(session.screen.drawing.draw_, memory=session.memory, value_handler=session.values, events=session.events),
            tk.TIMER: session.events.timer_,
            tk.IOCTL: session.files.ioctl_statement_,
            tk.CHDIR: session.devices.chdir_,
            tk.MKDIR: session.devices.mkdir_,
            tk.RMDIR: session.devices.rmdir_,
            tk.SHELL: session.shell_,
            tk.ENVIRON: dos.environ_statement_,
            tk.WINDOW: session.screen.drawing.window_,
            tk.LCOPY: session.devices.lcopy_,
            tk.PCOPY: session.screen.pcopy_,
            tk.LOCK: session.files.lock_,
            tk.UNLOCK: session.files.unlock_,
            tk.MID: session.memory.mid_,
            tk.PEN: session.events.pen_,
            tk.TERM: session.term_,
            tk.NOISE: session.sound.noise_,
            tk.ON + tk.ERROR: session.interpreter.on_error_goto_,
            tk.ON + tk.KEY: partial(session.events.on_event_gosub_, session.program),
            tk.ON + '\xFE': partial(session.events.on_event_gosub_, session.program),
            tk.ON + '\xFF': partial(session.events.on_event_gosub_, session.program),
            tk.ON: session.interpreter.on_jump_,
            tk.DEF + tk.FN: session.interpreter.def_fn_,
            tk.DEF + tk.USR: session.all_memory.def_usr_,
            tk.DEF: session.all_memory.def_seg_,
            tk.LINE + tk.INPUT: session.line_input_,
            tk.LINE: session.screen.drawing.line_,
            tk.KEY + tk.ON: partial(session.fkey_macros.key_, session.screen),
            tk.KEY + tk.OFF: partial(session.fkey_macros.key_, session.screen),
            tk.KEY + tk.LIST: partial(session.fkey_macros.key_, session.screen),
            tk.KEY + '(': session.events.key_,
            tk.KEY: session.key_,
            tk.PUT + '(': partial(session.screen.drawing.put_, session.arrays),
            tk.PUT: session.files.put_,
            tk.GET + '(': partial(session.screen.drawing.get_, session.arrays),
            tk.GET: session.files.get_,
            tk.PLAY + tk.ON: session.events.play_,
            tk.PLAY + tk.OFF: session.events.play_,
            tk.PLAY + tk.STOP: session.events.play_,
            tk.PLAY: partial(session.sound.play_, session.memory, session.values),
            tk.VIEW + tk.PRINT: session.screen.view_print_,
            tk.VIEW: session.screen.drawing.view_,
            tk.PALETTE + tk.USING: partial(session.screen.palette.palette_using_, session.arrays),
            tk.PALETTE: session.screen.palette.palette_,
            tk.STRIG + tk.ON: session.stick.strig_statement_,
            tk.STRIG + tk.OFF: session.stick.strig_statement_,
            tk.STRIG: session.events.strig_,
            '_DEBUG': session.debugger.debug_,
        }

    def __getstate__(self):
        """Pickle."""
        pickle_dict = self.__dict__.copy()
        # can't be pickled
        pickle_dict['_simple'] = None
        pickle_dict['_complex'] = None
        pickle_dict['_extensions'] = None
        pickle_dict['_callbacks'] = None
        return pickle_dict

    def __setstate__(self, pickle_dict):
        """Unpickle."""
        self.__dict__.update(pickle_dict)

    ###########################################################################
    # statements taking no arguments

    def _parse_nothing(self, ins):
        """Parse nothing."""
        # e.g. TRON LAH raises error but TRON will have been executed
        return
        yield

    def _parse_end(self, ins):
        """Parse end-of-statement before executing argumentless statement."""
        # e.g. SYSTEM LAH does not execute
        ins.require_end()
        # empty generator
        return
        yield

    def _skip_line(self, ins):
        """Ignore the rest of the line."""
        ins.skip_to(tk.END_LINE)
        return
        yield

    def _skip_statement(self, ins):
        """Ignore rest of statement."""
        ins.skip_to(tk.END_STATEMENT)
        return
        yield

    ###########################################################################
    # statements taking a single argument

    def _parse_optional_arg_iter(self, ins):
        """Parse statement with on eoptional argument."""
        yield self.parse_expression(ins, allow_empty=True)
        ins.require_end()

    def _parse_single_arg_iter(self, ins):
        """Parse statement with one mandatory argument."""
        yield self.parse_expression(ins)
        ins.require_end()

    def _parse_single_line_number_iter(self, ins):
        """Parse statement with single line number argument."""
        yield self._parse_jumpnum(ins)

    def _parse_optional_line_number_iter(self, ins):
        """Parse statement with optional line number argument."""
        jumpnum = None
        if ins.skip_blank() == tk.T_UINT:
            jumpnum = self._parse_jumpnum(ins)
        yield jumpnum

    def _parse_string_arg_iter(self, ins):
        """Parse DRAW syntax."""
        yield self._parse_temporary_string(ins)
        ins.require_end()

    def _parse_single_string_arg_iter(self, ins):
        """Parse statement with single string-valued argument."""
        yield self._parse_temporary_string(ins)

    def _parse_optional_string_arg_iter(self, ins):
        """Parse statement with single optional string-valued argument."""
        if ins.skip_blank() not in tk.END_STATEMENT:
            yield self._parse_temporary_string(ins)
        else:
            yield None

    ###########################################################################
    # flow-control statements

    def _parse_run_args_iter(self, ins):
        """Parse RUN syntax."""
        c = ins.skip_blank()
        if c == tk.T_UINT:
            # parse line number and ignore rest of line
            yield self._parse_jumpnum(ins)
            yield None
        elif c not in tk.END_STATEMENT:
            yield self._parse_temporary_string(ins)
            if ins.skip_blank_read_if((',',)):
                ins.require_read(('R',))
                yield True
            else:
                yield False
            ins.require_end()
        else:
            yield None
            yield None

    def _parse_resume_args_iter(self, ins):
        """Parse RESUME syntax."""
        c = ins.skip_blank()
        if c == tk.NEXT:
            yield ins.read(1)
        elif c in tk.END_STATEMENT:
            yield None
        else:
            yield self._parse_jumpnum(ins)
        ins.require_end()

    def _parse_on_error_goto_args_iter(self, ins):
        """Parse ON ERROR GOTO syntax."""
        ins.require_read((tk.ERROR,))
        ins.require_read((tk.GOTO,))
        yield self._parse_jumpnum(ins)

    ###########################################################################
    # event statements

    def _parse_event_command_iter(self, ins):
        """Parse PEN, PLAY or TIMER syntax."""
        yield ins.require_read((tk.ON, tk.OFF, tk.STOP))

    def _parse_com_command_iter(self, ins):
        """Parse KEY, COM or STRIG syntax."""
        yield self._parse_bracket(ins)
        yield ins.require_read((tk.ON, tk.OFF, tk.STOP))

    def _parse_strig_switch_iter(self, ins):
        """Parse STRIG ON/OFF syntax."""
        yield ins.require_read((tk.ON, tk.OFF))

    def _parse_on_event_args_iter(self, ins):
        """Helper function for ON event trap definitions."""
        token = ins.read_keyword_token()
        yield token
        if token not in (tk.PEN, tk.KEY, tk.TIMER, tk.PLAY, tk.COM, tk.STRIG):
            raise error.RunError(error.STX)
        if token != tk.PEN:
            yield self._parse_bracket(ins)
        else:
            yield None
        ins.require_read((tk.GOSUB,))
        yield self._parse_jumpnum(ins)
        ins.require_end()

    ###########################################################################
    # sound statements

    def _parse_beep_args_iter(self, ins):
        """Parse BEEP syntax."""
        if self.syntax in ('pcjr', 'tandy'):
            # Tandy/PCjr BEEP ON, OFF
            yield ins.skip_blank_read_if((tk.ON, tk.OFF))
        else:
            yield None
        # if a syntax error happens, we still beeped.

    def _parse_noise_args_iter(self, ins):
        """Parse NOISE syntax (Tandy/PCjr)."""
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)
        ins.require_end()

    def _parse_sound_args_iter(self, ins):
        """Parse SOUND syntax."""
        command = None
        if self.syntax in ('pcjr', 'tandy'):
            # Tandy/PCjr SOUND ON, OFF
            command = ins.skip_blank_read_if((tk.ON, tk.OFF))
        if command:
            yield command
        else:
            yield self.parse_expression(ins)
            ins.require_read((',',))
            dur = self.parse_expression(ins)
            yield dur
            # only look for args 3 and 4 if duration is > 0;
            # otherwise those args are a syntax error (on tandy)
            if (dur.sign() == 1) and ins.skip_blank_read_if((',',)) and self.syntax in ('pcjr', 'tandy'):
                yield self.parse_expression(ins)
                if ins.skip_blank_read_if((',',)):
                    yield self.parse_expression(ins)
                else:
                    yield None
            else:
                yield None
                yield None
        ins.require_end()

    def _parse_play_args_iter(self, ins):
        """Parse PLAY (music) syntax."""
        if self.syntax in ('pcjr', 'tandy'):
            for _ in range(3):
                last = self._parse_temporary_string(ins, allow_empty=True)
                yield last
                if not ins.skip_blank_read_if((',',)):
                    break
            else:
                raise error.RunError(error.STX)
            if last is None:
                raise error.RunError(error.MISSING_OPERAND)
            ins.require_end()
        else:
            yield self._parse_temporary_string(ins, allow_empty=True)
            ins.require_end(err=error.IFC)

    ###########################################################################
    # memory and machine port statements

    def _parse_def_seg_args_iter(self, ins):
        """Parse DEF SEG syntax."""
        # must be uppercase in tokenised form, otherwise syntax error
        ins.require_read((tk.W_SEG,))
        if ins.skip_blank_read_if((tk.O_EQ,)):
            yield self.parse_expression(ins)
        else:
            yield None

    def _parse_def_usr_args_iter(self, ins):
        """Parse DEF USR syntax."""
        ins.require_read((tk.USR))
        yield ins.skip_blank_read_if(tk.DIGIT)
        ins.require_read((tk.O_EQ,))
        yield self.parse_expression(ins)

    def _parse_poke_out_args_iter(self, ins):
        """Parse POKE or OUT syntax."""
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)

    def _parse_bload_args_iter(self, ins):
        """Parse BLOAD syntax."""
        yield self._parse_temporary_string(ins)
        if ins.skip_blank_read_if((',',)):
            yield self.parse_expression(ins)
        else:
            yield None
        ins.require_end()

    def _parse_bsave_args_iter(self, ins):
        """Parse BSAVE syntax."""
        yield self._parse_temporary_string(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)
        ins.require_end()

    def _parse_call_args_iter(self, ins):
        """Parse CALL and CALLS syntax."""
        yield self.parse_name(ins)
        if ins.skip_blank_read_if(('(',)):
            while True:
                yield self._parse_variable(ins)
                if not ins.skip_blank_read_if((',',)):
                    break
            ins.require_read((')',))
        ins.require_end()

    def _parse_wait_args_iter(self, ins):
        """Parse WAIT syntax."""
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)
        if ins.skip_blank_read_if((',',)):
            yield self.parse_expression(ins)
        else:
            yield None
        ins.require_end()

    ###########################################################################
    # disk statements

    def _parse_name_args_iter(self, ins):
        """Parse NAME syntax."""
        yield self._parse_temporary_string(ins)
        # AS is not a tokenised word
        ins.require_read((tk.W_AS,))
        yield self._parse_temporary_string(ins)

    ###########################################################################
    # clock statements

    def _parse_time_date_args_iter(self, ins):
        """Parse TIME$ or DATE$ syntax."""
        ins.require_read((tk.O_EQ,))
        yield self._parse_temporary_string(ins)
        ins.require_end()

    ##########################################################
    # code statements

    def _parse_line_range(self, ins):
        """Helper function: parse line number ranges."""
        from_line = self._parse_jumpnum_or_dot(ins, allow_empty=True)
        if ins.skip_blank_read_if((tk.O_MINUS,)):
            to_line = self._parse_jumpnum_or_dot(ins, allow_empty=True)
        else:
            to_line = from_line
        return (from_line, to_line)

    def _parse_jumpnum_or_dot(self, ins, allow_empty=False, err=error.STX):
        """Helper function: parse jump target."""
        c = ins.skip_blank_read()
        if c == tk.T_UINT:
            token = ins.read(2)
            assert len(token) == 2, 'bytecode truncated in line number pointer'
            return struct.unpack('<H', token)[0]
        elif c == '.':
            return self.session.program.last_stored
        else:
            if allow_empty:
                ins.seek(-len(c), 1)
                return None
            raise error.RunError(err)

    def _parse_delete_llist_args_iter(self, ins):
        """Parse DELETE syntax."""
        yield self._parse_line_range(ins)
        ins.require_end()

    def _parse_edit_args_iter(self, ins):
        """Parse EDIT syntax."""
        if ins.skip_blank() not in tk.END_STATEMENT:
            yield self._parse_jumpnum_or_dot(ins, err=error.IFC)
        else:
            yield None
        ins.require_end(err=error.IFC)

    def _parse_auto_args_iter(self, ins):
        """Parse AUTO syntax."""
        yield self._parse_jumpnum_or_dot(ins, allow_empty=True)
        if ins.skip_blank_read_if((',',)):
            inc = self._parse_optional_jumpnum(ins)
            if inc is None:
                raise error.RunError(error.IFC)
            else:
                yield inc
        else:
            yield None
        ins.require_end()

    def _parse_save_args_iter(self, ins):
        """Parse SAVE syntax."""
        yield self._parse_temporary_string(ins)
        if ins.skip_blank_read_if((',',)):
            yield ins.require_read(('A', 'a', 'P', 'p'))
        else:
            yield None

    def _parse_list_args_iter(self, ins):
        """Parse LIST syntax."""
        yield self._parse_line_range(ins)
        if ins.skip_blank_read_if((',',)):
            yield self._parse_temporary_string(ins)
            # ignore everything after file spec
            ins.skip_to(tk.END_LINE)
        else:
            yield None
            ins.require_end()

    def _parse_load_args_iter(self, ins):
        """Parse LOAD syntax."""
        yield self._parse_temporary_string(ins)
        if ins.skip_blank_read_if((',',)):
            yield ins.require_read(('R', 'r'))
        else:
            yield None
        ins.require_end()

    def _parse_renum_args_iter(self, ins):
        """Parse RENUM syntax."""
        new, old, step = None, None, None
        if ins.skip_blank() not in tk.END_STATEMENT:
            new = self._parse_jumpnum_or_dot(ins, allow_empty=True)
            if ins.skip_blank_read_if((',',)):
                old = self._parse_jumpnum_or_dot(ins, allow_empty=True)
                if ins.skip_blank_read_if((',',)):
                    step = self._parse_optional_jumpnum(ins)
        ins.require_end()
        if step is None:
            raise error.RunError(error.IFC)
        for n in (new, old, step):
            yield n

    def _parse_chain_args_iter(self, ins):
        """Parse CHAIN syntax."""
        yield ins.skip_blank_read_if((tk.MERGE,)) is not None
        yield self._parse_temporary_string(ins)
        jumpnum, common_all, delete_range = None, False, True
        if ins.skip_blank_read_if((',',)):
            # check for an expression that indicates a line in the other program.
            # This is not stored as a jumpnum (to avoid RENUM)
            jumpnum = self.parse_expression(ins, allow_empty=True)
            if ins.skip_blank_read_if((',',)):
                common_all = ins.skip_blank_read_if((tk.W_ALL,), 3)
                if common_all:
                    # CHAIN "file", , ALL, DELETE
                    delete_range = ins.skip_blank_read_if((',',))
                # CHAIN "file", , DELETE
        yield jumpnum
        yield common_all
        if delete_range and ins.skip_blank_read_if((tk.DELETE,)):
            from_line = self._parse_optional_jumpnum(ins)
            if ins.skip_blank_read_if((tk.O_MINUS,)):
                to_line = self._parse_optional_jumpnum(ins)
            else:
                to_line = from_line
            error.throw_if(not to_line)
            delete_lines = (from_line, to_line)
            # ignore rest if preceded by comma
            if ins.skip_blank_read_if((',',)):
                ins.skip_to(tk.END_STATEMENT)
            yield delete_lines
        else:
            yield None
        ins.require_end()

    ###########################################################################
    # file statements

    def _parse_open_args_iter(self, ins):
        """Parse OPEN syntax."""
        first_expr = self._parse_temporary_string(ins)
        if ins.skip_blank_read_if((',',)):
            args = self._parse_open_first(ins, first_expr)
        else:
            args = self._parse_open_second(ins, first_expr)
        for a in args:
            yield a

    def _parse_open_first(self, ins, first_expr):
        """Parse OPEN first ('old') syntax."""
        mode = first_expr[:1].upper()
        if mode not in ('I', 'O', 'A', 'R'):
            raise error.RunError(error.BAD_FILE_MODE)
        number = self._parse_file_number(ins, opt_hash=True)
        ins.require_read((',',))
        name = self._parse_temporary_string(ins)
        reclen = None
        if ins.skip_blank_read_if((',',)):
            reclen = self.parse_expression(ins)
        return number, name, mode, reclen, None, None

    def _parse_open_second(self, ins, first_expr):
        """Parse OPEN second ('new') syntax."""
        name = first_expr
        # FOR clause
        mode = None
        if ins.skip_blank_read_if((tk.FOR,)):
            # read mode word
            if ins.skip_blank_read_if((tk.INPUT,)):
                mode = 'I'
            else:
                word = ins.read_name()
                try:
                    mode = {tk.W_OUTPUT:'O', tk.W_RANDOM:'R', tk.W_APPEND:'A'}[word]
                except KeyError:
                    ins.seek(-len(word), 1)
                    raise error.RunError(error.STX)
        # ACCESS clause
        access = None
        if ins.skip_blank_read_if((tk.W_ACCESS,), 6):
            access = self._parse_read_write(ins)
        # LOCK clause
        if ins.skip_blank_read_if((tk.LOCK,), 2):
            lock = self._parse_read_write(ins)
        else:
            lock = ins.skip_blank_read_if((tk.W_SHARED), 6)
        # AS file number clause
        ins.require_read((tk.W_AS,))
        number = self._parse_file_number(ins, opt_hash=True)
        # LEN clause
        reclen = None
        if ins.skip_blank_read_if((tk.LEN,), 2):
            ins.require_read(tk.O_EQ)
            reclen = self.parse_expression(ins)
        return number, name, mode, reclen, access, lock

    def _parse_read_write(self, ins):
        """Parse access mode for OPEN."""
        d = ins.skip_blank_read_if((tk.READ, tk.WRITE))
        if d == tk.WRITE:
            return 'W'
        elif d == tk.READ:
            return 'RW' if ins.skip_blank_read_if((tk.WRITE,)) else 'R'
        raise error.RunError(error.STX)

    def _parse_close_args_iter(self, ins):
        """Parse CLOSE syntax."""
        if ins.skip_blank() not in tk.END_STATEMENT:
            while True:
                # if an error occurs, the files parsed before are closed anyway
                yield self._parse_file_number(ins, opt_hash=True)
                if not ins.skip_blank_read_if((',',)):
                    break

    def _parse_field_args_iter(self, ins):
        """Parse FIELD syntax."""
        yield self._parse_file_number(ins, opt_hash=True)
        if ins.skip_blank_read_if((',',)):
            while True:
                yield self.parse_expression(ins)
                ins.require_read((tk.W_AS,), err=error.IFC)
                yield self._parse_variable(ins)
                if not ins.skip_blank_read_if((',',)):
                    break

    def _parse_lock_unlock_args_iter(self, ins):
        """Parse LOCK or UNLOCK syntax."""
        yield self._parse_file_number(ins, opt_hash=True)
        if not ins.skip_blank_read_if((',',)):
            ins.require_end()
            yield None
            yield None
        else:
            expr = self.parse_expression(ins, allow_empty=True)
            yield expr
            if ins.skip_blank_read_if((tk.TO,)):
                yield self.parse_expression(ins)
            elif expr is not None:
                yield None
            else:
                raise error.RunError(error.MISSING_OPERAND)

    def _parse_ioctl_args_iter(self, ins):
        """Parse IOCTL syntax."""
        yield self._parse_file_number(ins, opt_hash=True)
        ins.require_read((',',))
        yield self._parse_temporary_string(ins)

    def _parse_put_get_file_args_iter(self, ins):
        """Parse PUT and GET syntax."""
        yield self._parse_file_number(ins, opt_hash=True)
        if ins.skip_blank_read_if((',',)):
            yield self.parse_expression(ins)
        else:
            yield None

    ###########################################################################
    # graphics statements

    def _parse_coord_bare(self, ins):
        """Parse coordinate pair."""
        ins.require_read(('(',))
        x = values.csng_(self.parse_expression(ins)).to_value()
        ins.require_read((',',))
        y = values.csng_(self.parse_expression(ins)).to_value()
        ins.require_read((')',))
        return x, y

    def _parse_coord_step(self, ins):
        """Parse coordinate pair with optional STEP."""
        step = ins.skip_blank_read_if((tk.STEP,))
        x, y = self._parse_coord_bare(ins)
        return x, y, step

    def _parse_pset_preset_args_iter(self, ins):
        """Parse PSET and PRESET syntax."""
        yield self._parse_coord_step(ins)
        if ins.skip_blank_read_if((',',)):
            yield self.parse_expression(ins)
        else:
            yield None
        ins.require_end()

    def _parse_window_args_iter(self, ins):
        """Parse WINDOW syntax."""
        screen = ins.skip_blank_read_if((tk.SCREEN,))
        yield screen
        if ins.skip_blank() == '(':
            yield self._parse_coord_bare(ins)
            ins.require_read((tk.O_MINUS,))
            yield self._parse_coord_bare(ins)
        elif screen:
            raise error.RunError(error.STX)
        else:
            yield None, None
            yield None, None

    def _parse_circle_args_iter(self, ins):
        """Parse CIRCLE syntax."""
        yield self._parse_coord_step(ins)
        ins.require_read((',',))
        last = self.parse_expression(ins)
        yield last
        for count_args in range(4):
            if ins.skip_blank_read_if((',',)):
                last = self.parse_expression(ins, allow_empty=True)
                yield last
            else:
                break
        if last is None:
            raise error.RunError(error.MISSING_OPERAND)
        for _ in range(count_args, 4):
            yield None
        ins.require_end()

    def _parse_paint_args_iter(self, ins):
        """Parse PAINT syntax."""
        yield self._parse_coord_step(ins)
        with self.temp_string:
            if ins.skip_blank_read_if((',',)):
                last = self.parse_expression(ins, allow_empty=True)
                yield last
                if ins.skip_blank_read_if((',',)):
                    last = self.parse_expression(ins, allow_empty=True)
                    yield last
                    if ins.skip_blank_read_if((',',)):
                        with self.temp_string:
                            yield self.parse_expression(ins)
                    elif last is None:
                        raise error.RunError(error.MISSING_OPERAND)
                    else:
                        yield None
                elif last is None:
                    raise error.RunError(error.MISSING_OPERAND)
                else:
                    yield None
                    yield None
            else:
                yield None
                yield None
                yield None

    def _parse_view_args_iter(self, ins):
        """Parse VIEW syntax."""
        yield ins.skip_blank_read_if((tk.SCREEN,))
        if ins.skip_blank() == '(':
            yield self._parse_coord_bare(ins)
            ins.require_read((tk.O_MINUS,))
            yield self._parse_coord_bare(ins)
            if ins.skip_blank_read_if((',',)):
                yield self.parse_expression(ins)
                ins.require_read((',',))
                yield self.parse_expression(ins)

    def _parse_line_args_iter(self, ins):
        """Parse LINE syntax."""
        if ins.skip_blank() in ('(', tk.STEP):
            yield self._parse_coord_step(ins)
        else:
            yield None
        ins.require_read((tk.O_MINUS,))
        yield self._parse_coord_step(ins)
        if ins.skip_blank_read_if((',',)):
            expr = self.parse_expression(ins, allow_empty=True)
            yield expr
            if ins.skip_blank_read_if((',',)):
                if ins.skip_blank_read_if(('B',)):
                    shape = 'BF' if ins.skip_blank_read_if(('F',)) else 'B'
                else:
                    shape = None
                yield shape
                if ins.skip_blank_read_if((',',)):
                    yield self._parse_value(ins, values.INT)
                else:
                    # mustn't end on a comma
                    # mode == '' if nothing after previous comma
                    error.throw_if(not shape, error.STX)
                    yield None
            elif not expr:
                raise error.RunError(error.MISSING_OPERAND)
            else:
                yield None
                yield None
        else:
            yield None
            yield None
            yield None
        ins.require_end()

    def _parse_get_graph_args_iter(self, ins):
        """Parse graphics GET syntax."""
        # don't accept STEP for first coord
        yield self._parse_coord_bare(ins)
        ins.require_read((tk.O_MINUS,))
        yield self._parse_coord_step(ins)
        ins.require_read((',',))
        yield self.parse_name(ins)
        ins.require_end()

    def _parse_put_graph_args_iter(self, ins):
        """Parse graphics PUT syntax."""
        # don't accept STEP
        yield self._parse_coord_bare(ins)
        ins.require_read((',',))
        yield self.parse_name(ins)
        if ins.skip_blank_read_if((',',)):
            yield ins.require_read((tk.PSET, tk.PRESET, tk.AND, tk.OR, tk.XOR))
        else:
            yield None
        ins.require_end()

    ###########################################################################
    # variable statements

    def _parse_clear_args_iter(self, ins):
        """Parse CLEAR syntax."""
        # integer expression allowed but ignored
        yield self.parse_expression(ins, allow_empty=True)
        if ins.skip_blank_read_if((',',)):
            exp1 = self.parse_expression(ins, allow_empty=True)
            yield exp1
            if not ins.skip_blank_read_if((',',)):
                if not exp1:
                    raise error.RunError(error.STX)
            else:
                # set aside stack space for GW-BASIC. The default is the previous stack space size.
                exp2 = self.parse_expression(ins, allow_empty=True)
                yield exp2
                if self.syntax in ('pcjr', 'tandy') and ins.skip_blank_read_if((',',)):
                    # Tandy/PCjr: select video memory size
                    yield self.parse_expression(ins)
                elif not exp2:
                    raise error.RunError(error.STX)
        ins.require_end()

    def _parse_common_args_iter(self, ins):
        """Parse COMMON syntax."""
        while True:
            name = self.parse_name(ins)
            brackets = ins.skip_blank_read_if(('[', '('))
            if brackets:
                ins.require_read((']', ')'))
            yield (name, brackets)
            if not ins.skip_blank_read_if((',',)):
                break

    def _parse_def_fn_args_iter(self, ins):
        """DEF FN: define a function."""
        ins.require_read((tk.FN))
        yield self.parse_name(ins)

    def _parse_var_list_iter(self, ins):
        """Generator: lazily parse variable list."""
        while True:
            yield self._parse_variable(ins)
            if not ins.skip_blank_read_if((',',)):
                break

    def _parse_var_list(self, ins):
        """Helper function: parse variable list."""
        return list(self._parse_var_list_iter(ins))

    def _parse_deftype_args_iter(self, ins):
        """Parse DEFSTR/DEFINT/DEFSNG/DEFDBL syntax."""
        while True:
            start = ins.require_read(string.ascii_letters)
            stop = None
            if ins.skip_blank_read_if((tk.O_MINUS,)):
                stop = ins.require_read(string.ascii_letters)
            yield start, stop
            if not ins.skip_blank_read_if((',',)):
                break

    def _parse_erase_args_iter(self, ins):
        """Parse ERASE syntax."""
        while True:
            yield self.parse_name(ins)
            if not ins.skip_blank_read_if((',',)):
                break

    def _parse_let_args_iter(self, ins):
        """Parse LET, LSET or RSET syntax."""
        yield self._parse_variable(ins)
        ins.require_read((tk.O_EQ,))
        # we're not using a temp string here
        # as it would delete the new string generated by let if applied to a code literal
        yield self.parse_expression(ins)

    def _parse_mid_args_iter(self, ins):
        """Parse MID$ syntax."""
        # do not use require_read as we don't allow whitespace here
        if ins.read(1) != '(':
            raise error.RunError(error.STX)
        yield self._parse_variable(ins)
        ins.require_read((',',))
        yield self._parse_value(ins, values.INT)
        if ins.skip_blank_read_if((',',)):
            yield self._parse_value(ins, values.INT)
        else:
            yield None
        ins.require_read((')',))
        ins.require_read((tk.O_EQ,))
        # we're not using a temp string here
        # as it would delete the new string generated by midset if applied to a code literal
        yield self.parse_expression(ins)
        ins.require_end()

    def _parse_option_base_args_iter(self, ins):
        """Parse OPTION BASE syntax."""
        ins.require_read((tk.W_BASE,))
        # MUST be followed by ASCII '1' or '0', num constants or expressions are an error!
        yield ins.require_read(('0', '1'))

    def _parse_prompt(self, ins):
        """Helper function for INPUT: parse prompt definition."""
        # ; to avoid echoing newline
        newline = not ins.skip_blank_read_if((';',))
        # parse prompt
        prompt, following = '', ';'
        if ins.skip_blank() == '"':
            # only literal allowed, not a string expression
            prompt = ins.read_string().strip('"')
            following = ins.require_read((';', ','))
        return newline, prompt, following

    def _parse_input_args_iter(self, ins):
        """Parse INPUT syntax."""
        file_number = self._parse_file_number(ins, opt_hash=False)
        yield file_number
        if file_number is not None:
            ins.require_read((',',))
        else:
            yield self._parse_prompt(ins)
        for arg in self._parse_var_list_iter(ins):
            yield arg

    def _parse_line_input_args_iter(self, ins):
        """Parse LINE INPUT syntax."""
        ins.require_read((tk.INPUT,))
        file_number = self._parse_file_number(ins, opt_hash=False)
        yield file_number
        if file_number is None:
            yield self._parse_prompt(ins)
        else:
            ins.require_read((',',))
        # get string variable
        yield self._parse_variable(ins)

    def _parse_restore_args_iter(self, ins):
        """Parse RESTORE syntax."""
        if ins.skip_blank() == tk.T_UINT:
            yield self._parse_jumpnum(ins)
            ins.require_end()
        else:
            # undefined line number for syntax errors if no line number given
            ins.require_end(err=error.UNDEFINED_LINE_NUMBER)
            yield None

    def _parse_swap_args_iter(self, ins):
        """Parse SWAP syntax."""
        yield self._parse_variable(ins)
        ins.require_read((',',))
        yield self._parse_variable(ins)

    ###########################################################################
    # console and editor statements

    def _parse_key_macro_args_iter(self, ins):
        """Parse KEY ON/OFF/LIST syntax."""
        yield ins.read_keyword_token()

    def _parse_key_define_args_iter(self, ins):
        """Parse KEY definition syntax."""
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self._parse_temporary_string(ins)

    def _parse_cls_args_iter(self, ins):
        """Parse CLS syntax."""
        if self.syntax != 'pcjr':
            yield self._parse_value(ins, values.INT, allow_empty=True)
            # optional comma
            if not ins.skip_blank_read_if((',',)):
                ins.require_end(err=error.IFC)
        else:
            yield None

    def _parse_color_args_iter(self, ins):
        """Parse COLOR syntax."""
        last = self._parse_value(ins, values.INT, allow_empty=True)
        yield last
        if ins.skip_blank_read_if((',',)):
            # unlike LOCATE, ending in any number of commas is a Missing Operand
            while True:
                last = self._parse_value(ins, values.INT, allow_empty=True)
                yield last
                if not ins.skip_blank_read_if((',',)):
                    break
            if last is None:
                raise error.RunError(error.MISSING_OPERAND)
        elif last is None:
            raise error.RunError(error.IFC)

    def _parse_palette_args_iter(self, ins):
        """Parse PALETTE syntax."""
        attrib = self._parse_value(ins, values.INT, allow_empty=True)
        yield attrib
        if attrib is None:
            yield None
            ins.require_end()
        else:
            ins.require_read((',',))
            colour = self._parse_value(ins, values.INT, allow_empty=True)
            yield colour
            error.throw_if(attrib is None or colour is None, error.STX)

    def _parse_palette_using_args_iter(self, ins):
        """Parse PALETTE USING syntax."""
        ins.require_read((tk.USING,))
        array_name, start_indices = self._parse_variable(ins)
        yield array_name, start_indices
        # brackets are not optional
        error.throw_if(not start_indices, error.STX)

    def _parse_locate_args_iter(self, ins):
        """Parse LOCATE syntax."""
        #row, col, cursor, start, stop
        for i in range(5):
            yield self._parse_value(ins, values.INT, allow_empty=True)
            # note that LOCATE can end on a 5th comma but no stuff allowed after it
            if not ins.skip_blank_read_if((',',)):
                break
        ins.require_end()

    def _parse_view_print_args_iter(self, ins):
        """Parse VIEW PRINT syntax."""
        ins.require_read((tk.PRINT,))
        start = self._parse_value(ins, values.INT, allow_empty=True)
        yield start
        if start is not None:
            ins.require_read((tk.TO,))
            yield self._parse_value(ins, values.INT)
        else:
            yield None
        ins.require_end()

    def _parse_write_args_iter(self, ins):
        """Parse WRITE syntax."""
        file_number = self._parse_file_number(ins, opt_hash=False)
        yield file_number
        if file_number is not None:
            ins.require_read((',',))
        with self.temp_string:
            expr = self.parse_expression(ins, allow_empty=True)
            if expr is not None:
                yield expr
        if expr is not None:
            while True:
                if not ins.skip_blank_read_if((',', ';')):
                    ins.require_end()
                    break
                with self.temp_string:
                    yield self.parse_expression(ins)

    def _parse_width_args_iter(self, ins):
        """Parse WIDTH syntax."""
        d = ins.skip_blank_read_if(('#', tk.LPRINT))
        if d:
            if d == '#':
                yield values.to_int(self.parse_expression(ins))
                ins.require_read((',',))
            else:
                yield tk.LPRINT
            yield self._parse_value(ins, values.INT)
        else:
            yield None
            with self.temp_string:
                if ins.peek() in set(string.digits) | set(tk.NUMBER):
                    expr = self.expression_parser.read_number_literal(ins)
                else:
                    expr = self.parse_expression(ins)
                yield expr
            if isinstance(expr, values.String):
                ins.require_read((',',))
                yield self._parse_value(ins, values.INT)
            else:
                if not ins.skip_blank_read_if((',',)):
                    yield None
                    ins.require_end(error.IFC)
                else:
                    # parse dummy number rows setting
                    yield self._parse_value(ins, values.INT, allow_empty=True)
                    # trailing comma is accepted
                    ins.skip_blank_read_if((',',))
        ins.require_end()

    def _parse_screen_args_iter(self, ins):
        """Parse SCREEN syntax."""
        # erase can only be set on pcjr/tandy 5-argument syntax
        #n_args = 4 + (self.syntax in ('pcjr', 'tandy'))
        # all but last arguments are optional and may be followed by a comma
        argcount = 0
        while True:
            last = self._parse_value(ins, values.INT, allow_empty=True)
            yield last
            argcount += 1
            if not ins.skip_blank_read_if((',',)):
                break
        if last is None:
            if self.syntax == 'tandy' and argcount == 1:
                raise error.RunError(error.IFC)
            raise error.RunError(error.MISSING_OPERAND)
        for _ in range(argcount, 5):
            yield None
        ins.require_end()

    def _parse_pcopy_args_iter(self, ins):
        """Parse PCOPY syntax."""
        yield self.parse_expression(ins)
        ins.require_read((',',))
        yield self.parse_expression(ins)
        ins.require_end()

    def _parse_print_args_iter(self, ins, parse_file):
        """Parse PRINT or LPRINT syntax."""
        if parse_file:
            # check for a file number
            file_number = self._parse_file_number(ins, opt_hash=False)
            yield file_number
            if file_number is not None:
                ins.require_read((',',))
        while True:
            d = ins.skip_blank_read()
            if d in tk.END_STATEMENT:
                ins.seek(-len(d), 1)
                break
            elif d == tk.USING:
                format_expr = self._parse_temporary_string(ins)
                if format_expr == '':
                    raise error.RunError(error.IFC)
                ins.require_read((';',))
                yield (tk.USING, format_expr)
                has_args = False
                while True:
                    with self.temp_string:
                        expr = self.parse_expression(ins, allow_empty=True)
                        yield expr
                        if expr is None:
                            ins.require_end()
                            # need at least one argument after format string
                            if not has_args:
                                raise error.RunError(error.MISSING_OPERAND)
                            break
                        has_args = True
                    if not ins.skip_blank_read_if((';', ',')):
                        break
                break
            elif d in (',', ';'):
                yield (d, None)
            elif d in (tk.SPC, tk.TAB):
                num = values.to_int(self.parse_expression(ins), unsigned=True)
                ins.require_read((')',))
                yield (d, num)
            else:
                ins.seek(-len(d), 1)
                with self.temp_string:
                    value = self.parse_expression(ins)
                    yield (None, value)

    ###########################################################################
    # loops and branches

    def _parse_on_jump_args_iter(self, ins):
        """ON: calculated jump."""
        yield self.parse_expression(ins)
        yield ins.require_read((tk.GOTO, tk.GOSUB))
        while True:
            num = self._parse_optional_jumpnum(ins)
            if num is None:
                break
            yield num
            if not ins.skip_blank_read_if((',',)):
                break
        ins.require_end()

    def _parse_if_args_iter(self, ins):
        """IF: enter branching statement."""
        # avoid overflow: don't use bools.
        condition = self.parse_expression(ins)
        ins.skip_blank_read_if((',',)) # optional comma
        ins.require_read((tk.THEN, tk.GOTO))
        yield condition
        # note that interpreter.if_ cofunction may jump to ELSE clause now
        if ins.skip_blank() in (tk.T_UINT,):
            yield self._parse_jumpnum(ins)
        else:
            yield None

    def _parse_for_args_iter(self, ins):
        """Parse FOR syntax."""
        # read variable
        yield self.parse_name(ins)
        ins.require_read((tk.O_EQ,))
        yield self.parse_expression(ins)
        ins.require_read((tk.TO,))
        yield self.parse_expression(ins)
        if ins.skip_blank_read_if((tk.STEP,)):
            yield self.parse_expression(ins)
        else:
            yield None
        ins.require_end()

    def _parse_next_args_iter(self, ins):
        """Parse NEXT syntax."""
        # note that next_ will not run the full generator if it finds a loop to iterate
        while True:
            # optional var name, errors have been checked during _find_next scan
            if ins.skip_blank() not in tk.END_STATEMENT + (',',):
                yield self.parse_name(ins)
            else:
                yield None
            # done if we're not jumping into a comma'ed NEXT
            if not ins.skip_blank_read_if((',')):
                break
