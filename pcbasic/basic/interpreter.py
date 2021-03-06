"""
PC-BASIC - interpreter.py
BASIC interpreter

(c) 2013, 2014, 2015, 2016 Rob Hagemans
This file is released under the GNU GPL version 3 or later.
"""

import string
import struct

from . import error
from . import tokens as tk
from . import codestream
from . import values


class Interpreter(object):
    """BASIC interpreter."""

    def __init__(self, session, program, statement_parser):
        """Initialise interpreter."""
        self.session = session
        # line number tracing
        self.tron = False
        # pointer position: False for direct line, True for program
        self.run_mode = False
        # program buffer
        self.program = program
        self.program_code = program.bytecode
        # direct line buffer
        self.direct_line = codestream.TokenisedStream()
        self.current_statement = 0
        # statement syntax parser
        self.statement_parser = statement_parser
        # clear stacks
        self.clear_stacks_and_pointers()
        self.init_error_trapping()
        self.error_num = 0
        self.error_pos = 0
        self.set_pointer(False, 0)

    def init_error_trapping(self):
        """Initialise error trapping."""
        # True if error handling in progress
        self.error_handle_mode = False
        # statement pointer, run mode of error for RESUME
        self.error_resume = None
        # pointer to error trap
        self.on_error = None

    def parse(self):
        """Parse from the current pointer in current codestream."""
        while True:
            # may raise Break
            self.session.events.check_events()
            try:
                self.handle_basic_events()
                ins = self.get_codestream()
                self.current_statement = ins.tell()
                c = ins.skip_blank_read()
                # parse line number or : at start of statement
                if c in tk.END_LINE:
                    # line number marker, new statement
                    token = ins.read(4)
                    # end of program or truncated file
                    if token[:2] == '\0\0' or len(token) < 4:
                        if self.error_resume:
                            # unfinished error handler: no RESUME (don't trap this)
                            self.error_handle_mode = True
                            # get line number right
                            raise error.RunError(error.NO_RESUME, ins.tell()-len(token)-2)
                        # stream has ended
                        self.set_pointer(False)
                        return
                    if self.tron:
                        linenum = struct.unpack_from('<H', token, 2)
                        self.session.screen.write('[%i]' % linenum)
                    self.session.debugger.debug_step(token)
                elif c != ':':
                    ins.seek(-len(c), 1)
                self.statement_parser.parse_statement(ins)
            except error.RunError as e:
                self.trap_error(e)

    ###########################################################################
    # clear state

    def clear(self):
        """Clear all to be cleared for CLEAR statement."""
        # clear last error number (ERR) and line number (ERL)
        self.error_num, self.error_pos = 0, 0
        # disable error trapping
        self.init_error_trapping()
        # disable all event trapping (resets PEN to OFF too)
        self.session.events.reset()
        # CLEAR also dumps for_next and while_wend stacks
        self.clear_loop_stacks()
        # reset the DATA pointer
        self.data_pos = 0

    def clear_stacks_and_pointers(self):
        """Initialise the stacks and pointers for a new program."""
        # stop running if we were
        self.set_pointer(False)
        # reset loop stacks
        self.clear_stacks()
        # reset program pointer
        self.program_code.seek(0)
        # reset stop/cont
        self.stop = None
        # reset data reader
        self.data_pos = 0

    def clear_stacks(self):
        """Clear loop and jump stacks."""
        self.gosub_stack = []
        self.clear_loop_stacks()

    def clear_loop_stacks(self):
        """Clear loop stacks."""
        self.for_stack = []
        self.while_stack = []

    ###########################################################################
    # event and error handling

    def handle_basic_events(self):
        """Jump to user-defined event subs if events triggered."""
        if self.session.events.suspend_all or not self.run_mode:
            return
        for event in self.session.events.enabled:
            if (event.triggered and not event.stopped and event.gosub is not None):
                # release trigger
                event.triggered = False
                # stop this event while handling it
                event.stopped = True
                # execute 'ON ... GOSUB' subroutine;
                # attach handler to allow un-stopping event on RETURN
                self.jump_sub(event.gosub, event)

    def trap_error(self, e):
        """Handle a BASIC error through trapping."""
        if e.pos is None:
            if self.run_mode:
                e.pos = self.program_code.tell()-1
            else:
                e.pos = -1
        self.error_num = e.err
        self.error_pos = e.pos
        # don't jump if we're already busy handling an error
        if self.on_error is not None and self.on_error != 0 and not self.error_handle_mode:
            self.error_resume = self.current_statement, self.run_mode
            self.jump(self.on_error)
            self.error_handle_mode = True
            self.session.events.suspend_all = True
        else:
            self.error_handle_mode = False
            self.set_pointer(False)
            raise e

    def erl_(self):
        """ERL: get line number of last error."""
        if self.error_pos == 0:
            return 0
        elif self.error_pos == -1:
            return 65535
        else:
            return self.program.get_line_number(self.error_pos)

    def err_(self):
        """ERR: get error code of last error."""
        return self.error_num

    ###########################################################################
    # jumps

    def set_pointer(self, new_runmode, pos=None):
        """Set program pointer to the given codestream and position."""
        self.run_mode = new_runmode
        # events are active in run mode
        self.session.events.set_active(new_runmode)
        # keep the sound engine on to avoid delays in run mode
        self.session.sound.persist(new_runmode)
        # suppress cassette messages in run mode
        self.session.devices.devices['CAS1:'].quiet(new_runmode)
        codestream = self.get_codestream()
        if pos is not None:
            # jump to position, if given
            codestream.seek(pos)
        else:
            # position at end - don't execute anything unless we jump
            codestream.seek(0, 2)

    def get_codestream(self):
        """Get the current codestream."""
        return self.program_code if self.run_mode else self.direct_line

    def jump(self, jumpnum, err=error.UNDEFINED_LINE_NUMBER):
        """Execute jump for a GOTO or RUN instruction."""
        if jumpnum is None:
            self.set_pointer(True, 0)
        else:
            try:
                # jump to target
                self.set_pointer(True, self.program.line_numbers[jumpnum])
            except KeyError:
                raise error.RunError(err)

    def jump_sub(self, jumpnum, handler=None):
        """Execute jump for a GOSUB."""
        # set return position
        pos = self.get_codestream().tell()
        self.jump(jumpnum)
        self.gosub_stack.append((pos, self.run_mode, handler))

    def goto_(self, args):
        """GOTO: jump to line number."""
        self.jump(*args)

    def gosub_(self, args):
        """GOSUB: jump to subroutine."""
        self.jump_sub(*args)

    def return_(self, args):
        """Execute jump for a RETURN."""
        jumpnum, = args
        try:
            pos, orig_runmode, handler = self.gosub_stack.pop()
        except IndexError:
            raise error.RunError(error.RETURN_WITHOUT_GOSUB)
        # returning from ON (event) GOSUB, re-enable event
        if handler:
            # if stopped explicitly using STOP, we wouldn't have got here; it STOP is run  inside the trap, no effect. OFF in trap: event off.
            handler.stopped = False
        if jumpnum is None:
            # go back to position of GOSUB
            self.set_pointer(orig_runmode, pos)
            # ignore rest of statement ('GOSUB 100 LAH' works just fine..)
            self.get_codestream().skip_to(tk.END_STATEMENT)
        else:
            # jump to specified line number
            self.jump(jumpnum)

    ###########################################################################
    # branches

    def if_(self, args):
        """IF: branching statement."""
        # get condition
        # avoid overflow: don't use bools.
        val = values.csng_(next(args))
        if val.is_zero():
            # find corrrect ELSE block, if any
            # ELSEs may be nested in the THEN clause
            ins = self.get_codestream()
            nesting_level = 0
            while True:
                d = ins.skip_to_read(tk.END_STATEMENT + (tk.IF,))
                if d == tk.IF:
                    # nesting step on IF. (it's less convenient to count THENs
                    # because they could be THEN or GOTO)
                    nesting_level += 1
                elif d == ':':
                    # :ELSE is ELSE; may be whitespace in between. no : means it's ignored.
                    if ins.skip_blank_read_if((tk.ELSE,)):
                        if nesting_level > 0:
                            nesting_level -= 1
                        else:
                            # read line number or continue execution
                            break
                else:
                    ins.seek(-len(d), 1)
                    break
        branch, = args
        # we may have a line number immediately after THEN or ELSE
        if branch is not None:
            self.jump(branch)
        # otherwise continue parsing as normal from next statement after THEN
        # note that any :ELSE block encountered will be ignored automatically
        # since standalone ELSE is a no-op to end of line

    def on_jump_(self, args):
        """ON GOTO/GOSUB: calculated jump."""
        onvar = values.to_int(next(args))
        error.range_check(0, 255, onvar)
        jump_type = next(args)
        # only parse jumps (and errors!) up to our choice
        i = -1
        for i, jumpnum in enumerate(args):
            if i == onvar-1:
                if jump_type == tk.GOTO:
                    self.jump(jumpnum)
                elif jump_type == tk.GOSUB:
                    self.jump_sub(jumpnum)
                return
        if i == onvar-2:
            # missing jump *just where we need it* is an error
            raise error.RunError(error.STX)

    ###########################################################################
    # loops

    def for_(self, args):
        """Initialise a FOR loop."""
        # read variable
        varname = next(args)
        vartype = varname[-1]
        start = values.to_type(vartype, next(args))
        # only raised after the TO has been parsed
        if vartype in (values.STR, values.DBL):
            raise error.RunError(error.TYPE_MISMATCH)
        stop = values.to_type(vartype, next(args))
        step = next(args)
        if step is not None:
            step = values.to_type(vartype, step)
        list(args)
        if step is None:
            # convert 1 to vartype
            step = self.session.values.from_value(1, varname[-1])
        ins = self.get_codestream()
        # find NEXT
        forpos, nextpos = self._find_next(ins, varname)
        # initialise loop variable
        self.session.scalars.set(varname, start)
        # obtain a view of the loop variable
        counter_view = self.session.scalars.view(varname)
        self.for_stack.append((counter_view, stop, step, step.sign(), forpos, nextpos,))
        # empty loop: jump to NEXT without executing block
        if (start.gt(stop) if step.sign() > 0 else stop.gt(start)):
            ins.seek(nextpos)
            self.iterate_loop()

    def _find_next(self, ins, varname):
        """Helper function for FOR: find matching NEXT."""
        endforpos = ins.tell()
        ins.skip_block(tk.FOR, tk.NEXT, allow_comma=True)
        if ins.skip_blank() not in (tk.NEXT, ','):
            # FOR without NEXT marked with FOR line number
            ins.seek(endforpos)
            raise error.RunError(error.FOR_WITHOUT_NEXT)
        comma = (ins.read(1) == ',')
        # check var name for NEXT
        # no-var only allowed in standalone NEXT
        if ins.skip_blank() not in tk.END_STATEMENT:
            varname2 = self.statement_parser.parse_name(ins)
        else:
            varname2 = None
        # get position and line number just after the matching variable in NEXT
        nextpos = ins.tell()
        if (comma or varname2) and varname2 != varname:
            # NEXT without FOR marked with NEXT line number, while we're only at FOR
            raise error.RunError(error.NEXT_WITHOUT_FOR)
        ins.seek(endforpos)
        return endforpos, nextpos

    def next_(self, args):
        """Iterate a loop (NEXT)."""
        for varname in args:
            # increment counter, check condition
            if self.iterate_loop(varname):
                break

    def iterate_loop(self, dummy_varname=None):
        """Iterate a loop (NEXT)."""
        ins = self.get_codestream()
        # record the location after the variable
        pos = ins.tell()
        # find the matching NEXT record
        num = len(self.for_stack)
        for depth in range(num):
            counter_view, stop, step, sgn, forpos, nextpos = self.for_stack[-depth-1]
            if pos == nextpos:
                # only drop NEXT record if we've found a matching one
                self.for_stack = self.for_stack[:len(self.for_stack)-depth]
                break
        else:
            raise error.RunError(error.NEXT_WITHOUT_FOR)
        # increment counter
        counter_view.iadd(step)
        # check condition
        loop_ends = counter_view.gt(stop) if sgn > 0 else stop.gt(counter_view)
        if loop_ends:
            self.for_stack.pop()
        else:
            ins.seek(forpos)
        return not loop_ends

    def while_(self, args):
        """WHILE: enter while-loop."""
        list(args)
        ins = self.get_codestream()
        # find matching WEND
        whilepos, wendpos = self._find_wend(ins)
        self.while_stack.append((whilepos, wendpos))
        self._check_while_condition(ins, whilepos)

    def _find_wend(self, ins):
        """Helper function for WHILE: find matching WEND."""
        # just after WHILE token
        whilepos = ins.tell()
        ins.skip_block(tk.WHILE, tk.WEND)
        if ins.read(1) != tk.WEND:
            # WHILE without WEND
            ins.seek(whilepos)
            raise error.RunError(error.WHILE_WITHOUT_WEND)
        ins.skip_to(tk.END_STATEMENT)
        wendpos = ins.tell()
        ins.seek(whilepos)
        return whilepos, wendpos

    def _check_while_condition(self, ins, whilepos):
        """Check condition of while-loop."""
        ins.seek(whilepos)
        # WHILE condition is zero?
        if not values.pass_number(self.statement_parser.parse_expression(ins)).is_zero():
            # statement start is before WHILE token
            self.current_statement = whilepos-2
            ins.require_end()
        else:
            # ignore rest of line and jump to WEND
            _, wendpos = self.while_stack.pop()
            ins.seek(wendpos)

    def wend_(self, args):
        """WEND: iterate while-loop."""
        list(args)
        ins = self.get_codestream()
        pos = ins.tell()
        while True:
            if not self.while_stack:
                # WEND without WHILE
                raise error.RunError(error.WEND_WITHOUT_WHILE)
            whilepos, wendpos = self.while_stack[-1]
            if pos == wendpos:
                break
            # not the expected WEND, we must have jumped out
            self.while_stack.pop()
        self._check_while_condition(ins, whilepos)

    ###########################################################################
    # DATA utilities

    def restore_(self, args):
        """Reset data pointer (RESTORE) """
        datanum = next(args)
        if datanum is None:
            self.data_pos = 0
        else:
            try:
                self.data_pos = self.program.line_numbers[datanum]
            except KeyError:
                raise error.RunError(error.UNDEFINED_LINE_NUMBER)
        list(args)

    def read_(self, args):
        """READ: read values from DATA statement."""
        for name, indices in args:
            type_char, code_start = name[-1], self.session.memory.code_start
            current = self.program_code.tell()
            self.program_code.seek(self.data_pos)
            if self.program_code.peek() in tk.END_STATEMENT:
                # initialise - find first DATA
                self.program_code.skip_to((tk.DATA,))
            if self.program_code.read(1) not in (tk.DATA, ','):
                self.program_code.seek(current)
                raise error.RunError(error.OUT_OF_DATA)
            self.program_code.skip_blank()
            word = self.program_code.read_to((',', '"',) + tk.END_LINE + tk.END_STATEMENT)
            if self.program_code.peek() == '"':
                if word == '':
                    word = self.program_code.read_string().strip('"')
                else:
                    word += self.program_code.read_string()
                if (self.program_code.skip_blank() not in (tk.END_STATEMENT + (',',))):
                    raise error.RunError(error.STX)
            else:
                word = word.strip(self.program_code.blanks)
            if type_char == values.STR:
                address = self.data_pos + code_start
                value = self.session.values.from_str_at(word, address)
            else:
                value = self.session.values.from_repr(word, allow_nonnum=False)
                if value is None:
                    # set pointer for EDIT gadget to position in DATA statement
                    self.program_code.seek(self.data_pos)
                    # syntax error in DATA line (not type mismatch!) if can't convert to var type
                    raise error.RunError(error.STX, self.data_pos-1)
            # omit leading and trailing whitespace
            data_pos = self.program_code.tell()
            self.program_code.seek(current)
            self.session.memory.set_variable(name, indices, value=value)
            self.data_pos = data_pos

    ###########################################################################
    # callbacks

    def system_(self, args):
        """SYSTEM: exit interpreter."""
        list(args)
        raise error.Exit()

    def stop_(self, args):
        """STOP: break program execution and return to interpreter."""
        list(args)
        raise error.Break(stop=True)

    def cont_(self, args):
        """CONT: continue STOPped or ENDed execution."""
        list(args)
        if self.stop is None:
            raise error.RunError(error.CANT_CONTINUE)
        else:
            self.set_pointer(True, self.stop)
        # IN GW-BASIC, weird things happen if you do GOSUB nn :PRINT "x"
        # and there's a STOP in the subroutine.
        # CONT then continues and the rest of the original line is executed, printing x
        # However, CONT:PRINT triggers a bug - a syntax error in a nonexistant line number is reported.
        # CONT:PRINT "y" results in neither x nor y being printed.
        # if a command is executed before CONT, x is not printed.
        # It would appear that GW-BASIC only partially overwrites the line buffer and
        # then jumps back to the original return location!
        # in this implementation, the CONT command will fully overwrite the line buffer so x is not printed.

    def tron_(self, args):
        """TRON: trace on."""
        list(args)
        self.tron = True

    def troff_(self, args):
        """TROFF: trace off."""
        list(args)
        self.tron = False

    def on_error_goto_(self, args):
        """ON ERROR GOTO: define error trapping routine."""
        linenum, = args
        if linenum != 0 and linenum not in self.program.line_numbers:
            raise error.RunError(error.UNDEFINED_LINE_NUMBER)
        self.on_error = linenum
        # pause soft-handling math errors so that we can catch them
        self.session.values.error_handler.suspend(linenum != 0)
        # ON ERROR GOTO 0 in error handler
        if self.on_error == 0 and self.error_handle_mode:
            # re-raise the error so that execution stops
            raise error.RunError(self.error_num, self.error_pos)

    def resume_(self, args):
        """RESUME: resume program flow after error-trap."""
        if self.error_resume is None:
            # unset error handler
            self.on_error = 0
            raise error.RunError(error.RESUME_WITHOUT_ERROR)
        # parse arguments
        where, = args
        start_statement, runmode = self.error_resume
        self.error_num = 0
        self.error_handle_mode = False
        self.error_resume = None
        self.session.events.suspend_all = False
        if not where:
            # RESUME or RESUME 0
            self.set_pointer(runmode, start_statement)
        elif where == tk.NEXT:
            # RESUME NEXT
            self.set_pointer(runmode, start_statement)
            self.get_codestream().skip_to(tk.END_STATEMENT, break_on_first_char=False)
        else:
            # RESUME n
            self.jump(where)

    def def_fn_(self, args):
        """DEF FN: define a function."""
        fnname, = args
        # don't allow DEF FN in direct mode, as we point to the code in the stored program
        # this is raised before further syntax errors
        if not self.run_mode:
            raise error.RunError(error.ILLEGAL_DIRECT)
        # arguments and expression are being read and parsed by UserFunctionManager
        self.statement_parser.expression_parser.user_functions.define(fnname, self.program_code)
