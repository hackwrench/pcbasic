"""
PC-BASIC - ports.py
Serial and parallel port handling

(c) 2013, 2014, 2015, 2016 Rob Hagemans
This file is released under the GNU GPL version 3 or later.

SocketSerialWrapper.read is modelled on Python 2.7 licensed code from PySerial
PySerial (c) 2001-2013 Chris Liechtl <cliechti(at)gmx.net>; All Rights Reserved.
"""

import logging
import sys
import os
import socket
import datetime
import platform
import io

# kbhit() also appears in video_none.py
if platform.system() == 'Windows':
    from msvcrt import kbhit
else:
    import select

    def kbhit():
        """Return whether a character is ready to be read from the keyboard."""
        return select.select([sys.stdin], [], [], 0)[0] != []

try:
    import serial
    from serial import SerialException, serialutil
except Exception:
    serial = None
    SerialException = IOError

try:
    import parallel
except Exception:
    parallel = None

from . import error
from . import devices
from . import printer


###############################################################################
# COM ports

class COMDevice(devices.Device):
    """Serial port device (COMn:)."""

    allowed_modes = 'IOAR'

    def __init__(self, arg, events, field, serial_in_size):
        """Initialise COMn: device."""
        devices.Device.__init__(self)
        addr, val = devices.parse_protocol_string(arg)
        self.stream = None
        self.events = events
        self.field = field
        self.serial_in_size = serial_in_size
        try:
            if not addr and not val:
                pass
            elif addr == 'SOCKET':
                self.stream = SocketSerialStream(val, self.events, do_open=False)
            elif addr == 'STDIO' or (not addr and val.upper() == 'STDIO'):
                crlf = (val.upper() == 'CRLF')
                self.stream = StdIOStream(crlf)
            elif addr == 'PORT':
                # port can be e.g. /dev/ttyS1 on Linux or COM1 on Windows.
                self.stream = SerialStream(val, self.events, do_open=False)
            else:
                logging.warning('Could not attach %s to COM device', arg)
        except (ValueError, EnvironmentError) as e:
            logging.warning('Could not attach %s to COM device: %s', arg, e)
            self.stream = None
        except AttributeError:
            logging.warning('Serial module not available. Could not attach %s to COM device: %s.', arg, e)
            self.stream = None
        if self.stream:
            # NOTE: opening a text file automatically tries to read a byte
            self.device_file = COMFile(self.stream, self.field, self.events, False, serial_in_size)

    def open(self, number, param, filetype, mode, access, lock,
                       reclen, seg, offset, length):
        """Open a file on COMn: """
        if not self.stream:
            raise error.RunError(error.DEVICE_UNAVAILABLE)
        # PE setting not implemented
        speed, parity, bytesize, stop, rs, cs, ds, cd, lf, _ = self.get_params(param)
        # open the COM port
        if self.stream.is_open:
            raise error.RunError(error.FILE_ALREADY_OPEN)
        else:
            try:
                self.stream.open(rs, cs, ds, cd)
            except EnvironmentError as e:
                # device timeout
                logging.debug("Serial exception: %s", e)
                raise error.RunError(error.DEVICE_TIMEOUT)
        try:
            self.stream.set_params(speed, parity, bytesize, stop)
        except Exception:
            self.stream.close()
            raise
        f = COMFile(self.stream, self.field, self.events, lf, self.serial_in_size)
        # inherit width settings from device file
        f.width = self.device_file.width
        f.col = self.device_file.col
        return f

    def get_params(self, param):
        """Parse serial port connection parameters """
        max_param = 10
        param_list = param.upper().split(',')
        if len(param_list) > max_param:
            raise error.RunError(error.BAD_FILE_NAME)
        param_list += ['']*(max_param-len(param_list))
        speed, parity, data, stop = param_list[:4]
        # set speed
        if speed not in ('75', '110', '150', '300', '600', '1200',
                          '1800', '2400', '4800', '9600', ''):
            # Bad file name
            raise error.RunError(error.BAD_FILE_NAME)
        speed = int(speed) if speed else 300
        # set parity
        if parity not in ('S', 'M', 'O', 'E', 'N', ''):
            raise error.RunError(error.BAD_FILE_NAME)
        parity = parity or 'E'
        # set data bits
        if data not in ('4', '5', '6', '7', '8', ''):
            raise error.RunError(error.BAD_FILE_NAME)
        data = int(data) if data else 7
        bytesize = data + (parity != 'N')
        if bytesize not in range(5, 9):
            raise error.RunError(error.BAD_FILE_NAME)
        # set stopbits
        if stop not in ('1', '2', ''):
            raise error.RunError(error.BAD_FILE_NAME)
        if not stop:
            stop = 2 if (speed in (75, 110)) else 1
        else:
            stop = int(stop)
        lf, rs, cs, ds, cd, pe = False, False, None, 1000, 0, False
        for named_param in param_list[4:]:
            if not named_param:
                continue
            try:
                if named_param == 'RS':
                    # suppress request to send
                    rs = True
                elif named_param[:2] == 'CS':
                    # set CTS timeout - clear to send
                    # 0 for empty string; BAD FILE NAME if not numeric
                    cs = int(named_param[2:]) if named_param[2:] else 0
                elif named_param[:2] == 'DS':
                    # set DSR timeout - data set ready
                    ds = int(named_param[2:]) if named_param[2:] else 0
                elif named_param[:2] == 'CD':
                    # set CD timeout - carrier detect
                    cd = int(named_param[2:]) if named_param[2:] else 0
                elif named_param == 'LF':
                    # send a line feed at each return
                    lf = True
                elif named_param == 'PE':
                    # enable parity checking
                    # not implemented
                    pe = True
                else:
                    raise error.RunError(error.BAD_FILE_NAME)
            except ValueError:
                raise error.RunError(error.BAD_FILE_NAME)
        # CS default depends on RS
        if cs is None:
            cs = 1000 if not rs else 0
        return speed, parity, bytesize, stop, rs, cs, ds, cd, lf, pe

    def char_waiting(self):
        """Whether a char is present in buffer. For ON COM(n)."""
        if not self.device_file:
            return False
        return self.device_file.in_buffer != ''


class COMFile(devices.CRLFTextFileBase):
    """COMn: device - serial port."""

    def __init__(self, fhandle, field, events, linefeed, serial_in_size):
        """Initialise COMn: file."""
        # note that for random files, fhandle must be a seekable stream.
        devices.CRLFTextFileBase.__init__(self, fhandle, 'D', 'R')
        # create a FIELD for GET and PUT. no text file operations on COMn: FIELD
        self.field = field
        # for wait()
        self.events = events
        self.serial_in_size = serial_in_size
        self.in_buffer = bytearray()
        self.linefeed = linefeed
        self.overflow = False

    def _check_read(self, allow_overflow=False):
        """Fill buffer at most up to buffer size; non blocking."""
        try:
            self.in_buffer += self.fhandle.read(self.serial_in_size - len(self.in_buffer))
        except (EnvironmentError, ValueError):
            raise error.RunError(error.DEVICE_IO_ERROR)
        # if more to read, signal an overflow
        if len(self.in_buffer) >= self.serial_in_size and self.fhandle.read(1):
            self.overflow = True
            # drop waiting chars that don't fit in buffer
            while self.fhandle.read(1):
                pass
        if not allow_overflow and self.overflow:
            # only raise this the first time the overflow is encountered
            self.overflow = False
            raise error.RunError(error.COMMUNICATION_BUFFER_OVERFLOW)

    def read_raw(self, num=-1):
        """Read num characters from the port as a string; blocking """
        if num == -1:
            # read whole buffer, non-blocking
            self._check_read()
            out = self.in_buffer
            del self.in_buffer[:]
        else:
            out = ''
            while len(out) < num:
                # non blocking read
                self._check_read()
                to_read = min(len(self.in_buffer), num - len(out))
                out += str(self.in_buffer[:to_read])
                del self.in_buffer[:to_read]
                # allow for break & screen updates
                # this also allows triggering BASIC events
                self.events.wait()
        return out

    def read_line(self):
        """Blocking read line from the port (not the FIELD buffer!)."""
        out = bytearray('')
        while len(out) < 255:
            c = self.read(1)
            if c == '\r':
                if self.linefeed:
                    c = self.read(1)
                    if c == '\n':
                        break
                    out += ''.join(c)
                else:
                    break
            out += ''.join(c)
        return out

    def write_line(self, s=''):
        """Write string or bytearray and newline to port."""
        self.write(str(s) + '\r')

    def write(self, s):
        """Write string to port."""
        try:
            if self.linefeed:
                s = s.replace('\r', '\r\n')
            self.fhandle.write(s)
        except (EnvironmentError, ValueError):
            raise error.RunError(error.DEVICE_IO_ERROR)

    def get(self, num):
        """Read a record - GET."""
        # blocking read of num bytes
        self.field.buffer[:] = self.read(num)

    def put(self, num):
        """Write a record - PUT."""
        self.write(self.field.buffer[:num])

    def loc(self):
        """LOC: Returns number of chars waiting to be read."""
        # don't use inWaiting() as SocketSerial.inWaiting() returns dummy 0
        # fill up buffer insofar possible
        self._check_read(allow_overflow=True)
        return len(self.in_buffer)

    def eof(self):
        """EOF: no chars waiting."""
        # for EOF(i)
        return self.loc() <= 0

    def lof(self):
        """Returns number of bytes free in buffer."""
        return self.serial_in_size - self.loc()


class StdIOStream(object):
    """Wrapper object to route port to stdio."""

    def __init__(self, crlf=False):
        """Initialise the stream."""
        self.is_open = False
        self._crlf = crlf

    def open(self, rs=False, cs=1000, ds=1000, cd=0):
        """Open a connection."""
        self.is_open = True

    def close(self):
        """Close the connection."""
        self.is_open = False

    def read(self, num=1):
        """Non-blocking read of up to `num` chars from stdin."""
        s = ''
        while kbhit() and len(s) < num:
            c = sys.stdin.read(1)
            if self._crlf and c == '\n':
                c = '\r'
            s += c
        return s

    def write(self, s):
        """Write to stdout."""
        for c in s:
            if self._crlf and c == '\r':
                c = '\n'
            sys.stdout.write(c)
        self.flush()

    def flush(self):
        """Flush stdout."""
        sys.stdout.flush()

    def set_params(self, speed, parity, bytesize, stop):
        """Set serial port connection parameters """

    def get_params(self):
        """Get serial port connection parameters """
        return 300, 'E', 8, 2

    def set_pins(self, rts=None, dtr=None, brk=None):
        """Set signal pins."""

    def get_pins(self):
        """Get signal pins."""
        return False, False, False, False

    def set_control(self, select=False, init=False, lf=False, strobe=False):
        """Set the values of the control pins."""

    def get_status(self):
        """Get the values of the status pins."""
        return False, False, False, False, False

    def io_waiting(self):
        """ Find out whether bytes are waiting for input or output. """
        return kbhit(), False


class SerialStream(object):
    """Wrapper object for Serial to enable pickling."""

    def __init__(self, port, events, do_open):
        """Initialise the stream."""
        self._serial = serial.serial_for_url(port, timeout=0, do_not_open=not do_open)
        # for wait()
        self._events = events
        self._url = port
        self.is_open = False

    def __getstate__(self):
        """Get pickling dict for stream."""
        return {'events': self._events, 'url': self._url, 'is_open': self.is_open}

    def __setstate__(self, st):
        """Initialise stream from pickling dict."""
        try:
            SerialStream.__init__(self, st['url'], st['events'], st['is_open'])
        except (EnvironmentError, ValueError) as e:
            logging.warning('Could not resume serial connection: %s', e)
            self.__init__(st['url'], st['events'], False)
            self.is_open = False

    # delegation doesn't play ball nicely with Pickle
    # def __getattr__(self, attr):
    #     return getattr(self._serial, attr)

    def _check_open(self):
        """Open the underlying port if necessary."""
        if not self._serial._isOpen:
            self._serial.open()

    def open(self, rs=False, cs=1000, ds=1000, cd=0):
        """Open the serial connection."""
        self._check_open()
        # handshake
        # by default, RTS is up, DTR down
        # RTS can be suppressed, DTR only accessible through machine ports
        # https://lbpe.wikispaces.com/AccessingSerialPort
        if not rs:
            self._serial.setRTS(True)
        now = datetime.datetime.now()
        timeout_cts = now + datetime.timedelta(microseconds=cs)
        timeout_dsr = now + datetime.timedelta(microseconds=ds)
        timeout_cd = now + datetime.timedelta(microseconds=cd)
        have_cts, have_dsr, have_cd = False, False, False
        while ((now < timeout_cts and not have_cts) and
                (now < timeout_dsr and not have_dsr) and
                (now < timeout_cd and not have_cd)):
            now = datetime.datetime.now()
            have_cts = have_cts and self._serial.getCTS()
            have_dsr = have_dsr and self._serial.getDSR()
            have_cts = have_cd and self._serial.getCD()
            # give CPU some time off
            self._events.wait()
        # only check for status if timeouts are set > 0
        # http://www.electro-tech-online.com/threads/qbasic-serial-port-control.19286/
        # https://measurementsensors.honeywell.com/ProductDocuments/Instruments/008-0385-00.pdf
        if ((cs > 0 and not have_cts) or
                (ds > 0 and not have_dsr) or
                (cd > 0 and not have_cd)):
            raise error.RunError(error.DEVICE_TIMEOUT)
        self.is_open = True

    def set_params(self, speed, parity, bytesize, stop):
        """Set serial port connection parameters."""
        self._check_open()
        self._serial.baudrate = speed
        self._serial.parity = parity
        self._serial.bytesize = bytesize
        self._serial.stopbits = stop

    def get_params(self):
        """Get serial port connection parameters."""
        self._check_open()
        return (self._serial.baudrate, self._serial.parity,
                self._serial.bytesize, self._serial.stopbits)

    def set_pins(self, rts=None, dtr=None, brk=None):
        """Set signal pins."""
        self._check_open()
        if rts is not None:
            self._serial.setRTS(rts)
        if dtr is not None:
            self._serial.setDTR(dtr)
        if brk is not None:
            self._serial.setBreak(brk)

    def get_pins(self):
        """Get signal pins."""
        self._check_open()
        return (self._serial.getCD(), self._serial.getRI(),
                self._serial.getDSR(), self._serial.getCTS())

    def close(self):
        """Close the serial connection."""
        self._serial.close()
        self.is_open = False

    def flush(self):
        """No buffer to flush."""
        pass

    def read(self, num=1):
        """Non-blocking read from socket."""
        self._check_open()
        # NOTE: num=1 follows PySerial
        # stream default is num=-1 to mean all available
        # but that's ill-defined for ports
        return self._serial.read(num)

    def write(self, s):
        """Write to socket."""
        self._check_open()
        self._serial.write(s)

    def io_waiting(self):
        """ Find out whether bytes are waiting for input or output. """
        self._check_open()
        return self._serial.inWaiting() > 0, self._serial.outWaiting() > 0


class SocketSerialStream(SerialStream):
    """Wrapper object for SocketSerial to work around timeout==0 issues."""

    def __init__(self, socket, events, do_open):
        """Initialise the stream."""
        SerialStream.__init__(self, 'socket://' + socket, events, do_open)

    def open(self, rs=False, cs=1000, ds=1000, cd=0):
        """Open the serial connection."""
        self._serial.open()
        self.is_open = True

    def read(self, num=1):
        """Non-blocking read from socket."""
        # SocketSerial.read always returns '' if timeout==0
        if not self._serial._isOpen:
            # this is a ValueError for some reason, not an IOError
            # but also raised by Serial so best to toe the line
            raise serialutil.portNotOpenError
        self._serial._socket.setblocking(0)
        try:
            # fill buffer at most up to buffer size
            return self._serial._socket.recv(num)
        except socket.timeout:
            return ''
        except socket.error as e:
            # a timeout in fact raises a socket.error 11
            # rather than a socket.timeout (at least on Linux)
            if e.errno == 11:
                return ''
            raise SerialException('connection failed (%s)' % e)



###############################################################################
# LPT ports

class LPTDevice(devices.Device):
    """Parallel port or printer device (LPTn:) """

    # LPT1 can be opened as RANDOM
    # but behaves just like OUTPUT
    # in GW-BASIC, FIELD gives a FIELD OVERFLOW; we get BAD FILE MODE.
    allowed_modes = 'OR'

    def __init__(self, arg, default_stream, flush_trigger, codepage, temp_dir):
        """Initialise LPTn: device."""
        devices.Device.__init__(self)
        addr, val = devices.parse_protocol_string(arg)
        self.stream = default_stream
        if addr == 'FILE':
            try:
                self.stream = open(val, 'wb')
            except EnvironmentError as e:
                logging.warning('Could not attach file %s to LPT device: %s', val, str(e))
        elif addr == 'PARPORT':
            # port can be e.g. /dev/parport0 on Linux or LPT1 on Windows. Just a number counting from 0 would also work.
            try:
                self.stream = ParallelStream(val)
            except EnvironmentError as e:
                logging.warning('Could not attach parallel port %s to LPT device: %s', val, str(e))
        elif addr == 'STDIO' or (not addr and val == 'STDIO'):
            crlf = (val.upper() == 'CRLF')
            self.stream = StdIOStream(crlf)
        elif addr == 'PRINTER' or (val and not addr):
            # 'PRINTER' is default
            self.stream = printer.get_printer_stream(val, codepage, temp_dir)
        elif val:
            logging.warning('Could not attach %s to LPT device', arg)
        if self.stream:
            self.device_file = LPTFile(self.stream, flush_trigger)
            self.device_file.flush_trigger = flush_trigger

    def open(self, number, param, filetype, mode, access, lock,
                   reclen, seg, offset, length):
        """Open a file on LPTn: """
        # don't trigger flushes on LPT files, just on the device directly
        f = LPTFile(self.stream, 'close')
        # inherit width settings from device file
        f.width = self.device_file.width
        f.col = self.device_file.col
        return f


class LPTFile(devices.TextFileBase):
    """LPTn: device - line printer or parallel port."""

    def __init__(self, stream, filetype='D', flush_trigger='close'):
        """Initialise LPTn."""
        devices.TextFileBase.__init__(self, io.BytesIO(), filetype, mode='A')
        # width=255 means line wrap
        self.width = 255
        self.col = 1
        self.output_stream = stream
        self.flush_trigger = flush_trigger

    def flush(self):
        """Flush the printer buffer to the underlying stream."""
        if self.fhandle:
            val = self.fhandle.getvalue()
            self.output_stream.write(val)
            self.fhandle.truncate(0)

    def write(self, s):
        """Write a string to the printer buffer."""
        for c in str(s):
            if self.col >= self.width and self.width != 255:  # width 255 means wrapping enabled
                self.fhandle.write('\r\n')
                self.flush()
                self.col = 1
            if c in ('\n', '\r', '\f'):
                # don't replace CR or LF with CRLF when writing to files
                self.fhandle.write(c)
                self.flush()
                self.col = 1
                # do the actual printing if we're on a short trigger
                if (self.flush_trigger == 'line' and c == '\n') or (self.flush_trigger == 'page' and c == '\f'):
                    self.output_stream.flush()
            elif c == '\b':   # BACKSPACE
                if self.col > 1:
                    self.col -= 1
                    self.fhandle.seek(-1, 1)
                    self.fhandle.truncate()
            else:
                self.fhandle.write(c)
                # nonprinting characters including tabs are not counted for WIDTH
                # for lpt1 and files , nonprinting chars are not counted in LPOS; but chr$(8) will take a byte out of the buffer
                if ord(c) >= 32:
                    self.col += 1

    def write_line(self, s=''):
        """Write string or bytearray and newline to file."""
        self.write(str(s) + '\r\n')

    def lof(self):
        """LOF: bad file mode """
        raise error.RunError(error.BAD_FILE_MODE)

    def loc(self):
        """LOC: bad file mode """
        raise error.RunError(error.BAD_FILE_MODE)

    def eof(self):
        """EOF: bad file mode """
        raise error.RunError(error.BAD_FILE_MODE)

    def close(self):
        """Close the printer device and actually print the output."""
        self.flush()
        self.output_stream.flush()
        self.fhandle.close()
        self.fhandle = None


class ParallelStream(object):
    """Wrapper for Parallel object to implement stream-like API."""

    def __init__(self, port):
        """Initialise the ParallelStream."""
        if not parallel:
            raise IOError('PySerial Parallel module not found. Parallel port communication not available.')
        try:
            self._parallel = parallel.Parallel(port)
        except TypeError:
            raise IOError('Invalid port specification.')
        self._port = port

    def __getstate__(self):
        """Get pickling dict for stream."""
        return { 'port': self._port }

    def __setstate__(self, st):
        """Initialise stream from pickling dict."""
        self.__init__(st['port'])

    def flush(self):
        """No buffer to flush."""
        pass

    def write(self, s):
        """Write to the parallel port."""
        if self._parallel.getInPaperOut():
            raise error.RunError(error.OUT_OF_PAPER)
        for c in s:
            self._parallel.setData(ord(c))

    def set_control(self, select=False, init=False, lf=False, strobe=False):
        """Set the values of the control pins."""
        self._parallel.setDataStrobe(strobe)
        self._parallel.setAutoFeed(lf)
        self._parallel.setInitOut(init)
        # select-printer pin not implemented

    def get_status(self):
        """Get the values of the status pins."""
        paper = self._parallel.getInPaperOut()
        ack = self._parallel.getInAcknowledge()
        select = self._parallel.getInSelected()
        # not implemented: busy, error pins
        busy = False
        err = False
        return busy, ack, paper, select, err

    def close(self):
        """Close the stream."""
        pass
