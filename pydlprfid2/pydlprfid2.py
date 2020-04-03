# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: nil; c-basic-offset: 4 -*-
# vim:fenc=utf-8:et:sw=4:ts=4:sts=4:tw=0

import re
import time
import serial
import pprint
import logging
import binascii

try:
    # Use colored logging if termcolor is available
    from termcolor import colored
except ImportError:
    # But just pass through the message if not
    def colored(msg, *args, **kwargs):
        return msg

from .crc import CRC


ISO15693 = 'ISO15693'
ISO14443A = 'ISO14443A'
ISO14443B = 'ISO14443B'

DLP_CMD = {
        "WRITEREG":   {"code": '10', "desc": "Write register"},
        "INV15693":   {"code": '14', "desc": "ISO15693 inventory"},
        "RAWWRITE":   {"code": '18', "desc": ("Everything after the 18 is what is"
                                              "actually transmitted over the air")},
        "INTERNANT":  {"code": '2A', "desc": "Enable internal antenna"},
        "EXTERNANT":  {"code": '2B', "desc": "Enable external antenna"},
        "GPIOMUX":    {"code": '2C', "desc": "GPIO multiplexer config"},
        "GPIOCFG":    {"code": '2D', "desc": "GPIO terminaison config"},
        "INV14443A":  {"code": 'A0', "desc": "ISO14443A inventory"},
        "AGCSEL":     {"code": 'F0', "desc": "AGC selection"},
        "AMPMSEL":    {"code": 'F1', "desc": "AM/PM input selection"},
        "SETLED2":    {"code": 'FB', "desc": "Set Led 2"},
        "SETLED3":    {"code": 'F9', "desc": "Set Led 3"},
        "SETLED4":    {"code": 'F7', "desc": "Set Led 4"},
        "SETLED5":    {"code": 'F5', "desc": "Set Led 5"},
        "SETLED6":    {"code": 'F3', "desc": "Set Led 6"},
        "CLRLED2":    {"code": 'FC', "desc": "Clear Led 2"},
        "CLRLED3":    {"code": 'FA', "desc": "Clear Led 3"},
        "CLRLED4":    {"code": 'F8', "desc": "Clear Led 4"},
        "CLRLED5":    {"code": 'F6', "desc": "Clear Led 5"},
        "CLRLED6":    {"code": 'F4', "desc": "Clear Led 6"},
        "INITIALIZE": {"code": 'FF', "desc": "Initialize reader"},
}


# commands codes from datasheet m24lr64e-r.pdf page 78
M24LR64ER_CMD = {
        "INVENTORY":           {"code": 0x01, "desc": "Inventory"},
        "QUIET":               {"code": 0x02, "desc": "Stay Quiet"},
        "READ_SINGLE_BLOCK":   {"code": 0x20, "desc": "Read Single Block"},
        "WRITE_SINGLE_BLOCK":  {"code": 0x21, "desc": "Write Single Block"},
        "READ_MULTIPLE_BLOCK": {"code": 0x23, "desc": "Read Multiple Block"},
        "SELECT":              {"code": 0x25, "desc": "Select"},
        "RESET_TO_READY":      {"code": 0x26, "desc": "Reset to Ready"},
        "WRITE_AFI":           {"code": 0x27, "desc": "Write AFI"},
        "LOCK_AFI":            {"code": 0x28, "desc": "Lock AFI"},
        "WRITE_DSFID":         {"code": 0x29, "desc": "Write DSFID"},
        "LOCK_DSFID":          {"code": 0x2A, "desc": "Lock DSFID"},
        "GET_SYS_INFO":        {"code": 0x2B, "desc": "Get System Info"},

        "GET_MULT_BLOC_SEC_INFO":{"code": 0x2C, "desc": "Get Multiple Block Security Status"},
        "WRITE_SECT_PSWD":   {"code": 0xB1, "desc": "Write-sector Password"},
        "LOCK_SECT_PSWD":    {"code": 0xB2, "desc": "Lock-sector"},
        "PRESENT_SECT_PSWD": {"code": 0xB3, "desc": "Present-sector Password"},
        "FAST_READ_SINGLE_BLOCK": {"code": 0xC0, "desc": "Fast Read Single Block"},
        "FAST_INVENTORY_INIT":    {"code": 0xC1, "desc": "Fast Inventory Initiated"},
        "FAST_INIT":              {"code": 0xC2, "desc": "Fast Initiate"},
        "FAST_READ_MULT_BLOCK":   {"code": 0xC3, "desc": "Fast Read Multiple Block"},
        "INVENTORY_INIT":         {"code": 0xD1, "desc": "Inventory Initiated"},
        "INITIATE":               {"code": 0xD2, "desc": "Initiate"},
        "READCFG": {"code": 0xA0, "desc": "ReadCfg"},
        "WRITEEHCFG": {"code": 0xA1, "desc": "WriteEHCfg"},
        "SETRSTEHEN": {"code": 0xA2, "desc": "SetRstEHEn"},
        "CHECKEHEN" : {"code": 0xA3, "desc": "CheckEHEn"},
        "WRITEDOCFG": {"code": 0xA4, "desc": "WriteDOCfg"}
        }

def reverse_uid(uid):
    if len(uid) != 16:
        raise Exception(f"Wrong uid size {len(uid)}, should be 16")
    return (uid[-2:] +
            uid[-4:-2] +
            uid[-6:-4] +
            uid[-8:-6] +
            uid[-10:-8] +
            uid[-12:-10] +
            uid[-14:-12] +
            uid[-16:-14])

def flagsbyte(double_sub_carrier=False, high_data_rate=False, inventory=False,
              protocol_extension=False, afi=False, single_slot=False,
              option=False, select=False, address=False):
    # Method to construct the flags byte
    # Reference: TI TRF9770A Evaluation Module (EVM) User's Guide, p. 8
    #            <http://www.ti.com/litv/pdf/slou321a>
    bits = '0'                                  # bit 8 (RFU) is always zero
    bits += '1' if option else '0'              # bit 7
    if inventory:
        bits += '1' if single_slot else '0'     # bit 6
        bits += '1' if afi else '0'             # bit 5
    else:
        bits += '1' if address else '0'         # bit 6
        bits += '1' if select else '0'          # bit 5
    bits += '1' if protocol_extension else '0'  # bit 4
    bits += '1' if inventory else '0'           # bit 3
    bits += '1' if high_data_rate else '0'      # bit 2
    bits += '1' if double_sub_carrier else '0'  # bit 1
    return '%02X' % int(bits, 2)     # return hex byte


class PyDlpRfid2(object):
    BAUDRATE=115200
    STOP_BITS=serial.STOPBITS_ONE
    PARITY=serial.PARITY_NONE
    BYTESIZE=serial.EIGHTBITS

    def __init__(self, serial_port, loglevel=logging.INFO):
        self.protocol = None
        self.__log_config(loglevel)
        self.sp = serial.Serial(port=serial_port,
                                baudrate=self.BAUDRATE,
                                stopbits=self.STOP_BITS,
                                parity=self.PARITY,
                                bytesize=self.BYTESIZE,
                                timeout=0.1)

        if not self.sp:
            raise StandardError('Could not connect to serial port ' + serial_port)

        self.logger.debug('Connected to ' + self.sp.portstr)
        self.flush()

    def __log_config(self, loglevel):
        self.logger = logging.getLogger(__name__)
        # create console handler and set level to debug
        ch = logging.StreamHandler()
        ch.setLevel(loglevel)
        # create formatter
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        # add formatter to ch
        ch.setFormatter(formatter)

        # add ch to logger
        self.logger.addHandler(ch)
        self.logger.setLevel(loglevel)


    def enable_external_antenna(self):
        cmdstr = DLP_CMD["EXTERNANT"]["code"]
        self.issue_evm_command(cmd=cmdstr)

    def set_protocol(self, protocol=ISO15693):

        self.protocol = protocol

        # 1. Initialize reader: 0xFF
        # 0108000304 FF 0000
        initcmd = DLP_CMD["INITIALIZE"]["code"]
        self.issue_evm_command(cmd=initcmd)  # Should return "TRF7970A EVM"

        # self.issue_evm_command(cmd='10', prms='0121')
        # self.issue_evm_command(cmd='10', prms='0021')

        # Select protocol: 15693 with full power
        self.issue_evm_command(cmd=DLP_CMD["WRITEREG"]["code"],
                               prms='00210100')

        # Setting up registers:
        #   0x00 Chip Status Control: Set to 0x21 for full power, 0x31 for half power
        #   0x01 ISO Control: Set to 0x00 for ISO15693, 0x09 for ISO14443A, 0x0C for ISO14443B
        protocol_values = {
            ISO15693: '00',   # 01 for 1-out-of-256 modulation
            ISO14443A: '09',
            ISO14443B: '0C',
        }
        self.issue_evm_command(cmd=DLP_CMD["WRITEREG"]["code"],
                               prms='0021' + '01' + protocol_values[protocol])

        # 3. AGC selection (0xF0) : AGC enable (0x00)
        # 0109000304 F0 00 0000
        self.issue_evm_command(cmd=DLP_CMD["AGCSEL"]["code"], prms='00')

        # 4. AM/PM input selection (0xF1) : AM input (0xFF)
        # 0109000304 F1 FF 0000
        self.issue_evm_command(cmd=DLP_CMD['AMPMSEL']["code"], prms='FF')

    def enable_led(self, led_no):
        cmd_codes = {2: 'FB', 3: 'F9', 4: 'F7', 5: 'F5', 6: 'F3'}
        self.issue_iso15693_command(cmd=cmd_codes[led_no])

    def disable_led(self, led_no):
        cmd_codes = {2: 'FC', 3: 'FA', 4: 'F8', 5: 'F6', 6: 'F4'}
        self.issue_iso15693_command(cmd=cmd_codes[led_no])

    def inventory(self, **kwargs):
        if self.protocol == ISO15693:
            return self.inventory_iso15693(**kwargs)
        elif self.protocol == ISO14443A:
            return self.inventory_iso14443A(**kwargs)

    def inventory_iso14443A(self):
        """
        By sending a 0xA0 command to the EVM module, the module will carry out
        the whole ISO14443 anti-collision procedure and return the tags found.

            >>> Req type A (0x26)
            <<< ATQA (0x04 0x00)
            >>> Select all (0x93, 0x20)
            <<< UID + BCC

        """
        response = self.issue_evm_command(cmd=DLP_CMD["INV14443A"]["code"])

        for itm in response:
            iba = bytearray.fromhex(itm)
            # Assume 4-byte UID + 1 byte Block Check Character (BCC)
            if len(iba) != 5:
                self.logger.warn('Encountered tag with UID of unknown length')
                continue
            if iba[0] ^ iba[1] ^ iba[2] ^ iba[3] ^ iba[4] != 0:
                self.logger.warn('BCC check failed for tag')
                continue
            uid = itm[:8]  # hex string, so each byte is two chars

            self.logger.debug('Found tag: %s (%s) ', uid, itm[8:])
            yield uid

            # See https://github.com/nfc-tools/libnfc/blob/master/examples/nfc-anticol.c

    def inventory_iso15693(self, single_slot=False):
        # Command code 0x01: ISO 15693 Inventory request
        # Example: 010B000304 14 24 0100 0000
        response = self.issue_iso15693_command(cmd=DLP_CMD["INV15693"]["code"],
                                               flags=flagsbyte(inventory=True,
                                                               single_slot=single_slot),
                                               command_code='%02X'%M24LR64ER_CMD["INVENTORY"]["code"],
                                               data='00')
        for itm in response:
            itm = itm.split(',')
            if itm[0] == 'z':
                self.logger.debug('Tag conflict!')
            else:
                if len(itm[0]) == 16:
                    uid = itm[0]
                    rssi = itm[1]
                    self.logger.debug('Found tag: %s (%s) ', uid, rssi)
                    yield reverse_uid(uid), rssi

    def eeprom_read_single_block(self, uid, blocknum):
        response = self.issue_iso15693_command(cmd=DLP_CMD["RAWWRITE"]["code"],
                                   flags=flagsbyte(address=False),  # 32 (dec) <-> 20 (hex)
                                   command_code='%02X'%M24LR64ER_CMD["READ_SINGLE_BLOCK"]["code"],
                                   data='%02X' % (blocknum))
                                   #data=uid + '%02X' % (blocknum))
        print(response)
        return response

    def read_danish_model_tag(self, uid):
        # Command code 0x23: Read multiple blocks
        block_offset = 0
        number_of_blocks = 8
        response = self.issue_iso15693_command(cmd=DLP_CMD["RAWWRITE"]["code"],
                                     flags=flagsbyte(address=True),  # 32 (dec) <-> 20 (hex)
                                     command_code='%02X'%M24LR64ER_CMD["READ_MULTIPLE_BLOCK"]["code"],
                                     data=uid + '%02X%02X' % (block_offset, number_of_blocks))

        response = response[0]
        if response == 'z':
            return {'error': 'tag-conflict'}
        elif response == '':
            return {'error': 'read-failed'}

        response = [response[i:i+2] for i in range(2, len(response), 2)]

        if response[0] == '00':
            is_blank = True
        else:
            is_blank = False

        # Reference:
        # RFID Data model for libraries : Doc 067 (July 2005), p. 30
        # <http://www.biblev.no/RFID/dansk_rfid_datamodel.pdf>

        # RFID Data model for libraries (February 2009), p. 30
        # http://biblstandard.dk/rfid/dk/RFID_Data_Model_for_Libraries_February_2009.pdf
        version = response[0][0]    # not sure if this is really the way to do it
        if version != '0' and version != '1':
            print(response)
            return {'error': 'unknown-version: %s' % version}

        usage_type = {
            '0': 'acquisition',
            '1': 'for-circulation',
            '2': 'not-for-circulation',
            '7': 'discarded',
            '8': 'patron-card'
        }[response[0][1]]  # not sure if this is really the way to do it

        nparts = int(response[1], 16)
        partno = int(response[2], 16)
        itemid = ''.join([chr(int(x, 16)) for x in response[3:19]])
        crc = response[19:21]
        country = ''.join([chr(int(x, 16)) for x in response[21:23]])
        library = ''.join([chr(int(x, 16)) for x in response[23:32]])

        # CRC calculation:
        p1 = response[0:19]     # 19 bytes
        p2 = response[21:32]    # 11 bytes
        p3 = ['00', '00']       # need to add 2 empty bytes to get 19 + 13 bytes
        p = [int(x, 16) for x in p1 + p2 + p3]
        calc_crc = ''.join(CRC().calculate(p)[::-1])
        crc = ''.join(crc)

        return {
            'error': '',
            'is_blank': is_blank,
            'usage_type': usage_type,
            'uid': uid,
            'id': itemid.strip('\0'),
            'partno': partno,
            'nparts': nparts,
            'country': country,
            'library': library.strip('\0'),
            'crc': crc,
            'crc_ok': calc_crc == crc
        }

    def write_danish_model_tag(self, uid, data, max_attempts=20):
        block_number = 0
        blocks = []

        data_bytes = ['00' for x in range(32)]
        data_bytes[0] = '11'
        data_bytes[1] = '%02X' % data['partno']
        data_bytes[2] = '%02X' % data['nparts']
        dokid = ['%02X' % ord(c) for c in data['id']]
        data_bytes[3:3+len(dokid)] = dokid
        data_bytes[21:23] = ['%02X' % ord(c) for c in data['country']]
        libnr = ['%02X' % ord(c) for c in data['library']]
        data_bytes[23:23+len(libnr)] = libnr

        # CRC calculation:
        p1 = data_bytes[0:19]     # 19 bytes
        p2 = data_bytes[21:32]    # 11 bytes
        p3 = ['00', '00']       # need to add 2 empty bytes to get 19 + 13 bytes
        p = [int(x, 16) for x in p1 + p2 + p3]
        crc = CRC().calculate(p)[::-1]
        data_bytes[19:21] = crc

        print(data_bytes)

        for x in range(8):
            print(data_bytes[x*4:x*4+4])
            attempt = 1
            while not self.write_block(uid, x, data_bytes[x*4:x*4+4]):
                self.logger.warn('Attempt %d of %d: Write failed, retrying...' % (attempt, max_attempts))
                if attempt >= max_attempts:
                    return False
                else:
                    attempt += 1
                    time.sleep(1.0)
        return True

    def write_blocks_to_card(self, uid, data_bytes, offset=0, nblocks=8):
        for x in range(offset, nblocks):
            print(data_bytes[x*4:x*4+4])
            success = False
            attempts = 0
            max_attempts = 10
            while not success:
                attempts += 1
                success = self.write_block(uid, x, data_bytes[x*4:x*4+4])
                if not success:
                    self.logger.warn('Write failed, retrying')
                    if attempts > max_attempts:
                        self.logger.warn('Giving up!')
                        return False
                    # time.sleep(1.0)
        return True

    def erase_card(self, uid):
        data_bytes = ['00' for x in range(32)]
        return self.write_blocks_to_card(uid, data_bytes)

    def write_danish_model_patron_card(self, uid, data):
        block_number = 0
        blocks = []

        data_bytes = ['00' for x in range(32)]

        version = '1'
        usage_type = '8'
        data_bytes[0] = version + usage_type
        data_bytes[1] = '01'  # partno
        data_bytes[2] = '01'  # nparts
        userid = ['%02X' % ord(c) for c in data['user_id']]
        print('userid:', userid)
        data_bytes[3:3+len(userid)] = userid
        data_bytes[21:23] = ['%02X' % ord(c) for c in data['country']]
        libnr = ['%02X' % ord(c) for c in data['library']]
        data_bytes[23:23+len(libnr)] = libnr

        # CRC calculation:
        p1 = data_bytes[0:19]     # 19 bytes
        p2 = data_bytes[21:32]    # 11 bytes
        p3 = ['00', '00']       # need to add 2 empty bytes to get 19 + 13 bytes
        p = [int(x, 16) for x in p1 + p2 + p3]
        crc = CRC().calculate(p)[::-1]
        data_bytes[19:21] = crc

        print(data_bytes)

        return self.write_blocks_to_card(uid, data_bytes)

    def write_block(self, uid, block_number, data):
        if type(data) != list or len(data) != 4:
            raise StandardError('write_block got data of unknown type/length')

        response = self.issue_iso15693_command(cmd=DLP_CMD["RAWWRITE"]["code"],
                                               flags=flagsbyte(address=True),  # 32 (dec) <-> 20 (hex)
                                               command_code='%02X'%M24LR64ER_CMD["WRITE_SINGLE_BLOCK"]["code"],
                                               data='%s%02X%s' % (uid, block_number, ''.join(data)))
        if response[0] == '00':
            self.logger.debug('Wrote block %d successfully', block_number)
            return True
        else:
            return False

    def unlock_afi(self, uid):
        self.issue_iso15693_command(cmd=DLP_CMD["RAWWRITE"]["code"],
                                    flags=flagsbyte(address=False,
                                                    high_data_rate=True,
                                                    option=False),  # 32 (dec) <-> 20 (hex)
                                    command_code='%02X'%M24LR64ER_CMD["WRITE_AFI"]["code"],
                                    data='C2')

    def lock_afi(self, uid):
        self.issue_iso15693_command(cmd=DLP_CMD["RAWWRITE"]["code"],
                                    flags=flagsbyte(address=False,
                                                    high_data_rate=False,
                                                    option=False),  # 32 (dec) <-> 20 (hex)
                                    command_code='%02X'%M24LR64ER_CMD["WRITE_AFI"]["code"],
                                    data='07')

    def issue_evm_command(self, cmd, prms=''):
        # The EVM protocol has a general form as shown below:
        #  1. SOF (Start of File): 0x01
        #  2. LENGTH : Two bytes define the number of bytes in the frame including SOF. Least Significant Byte first!
        #  3. READER_TYPE : 0x03
        #  4. ENTITY : 0x04
        #  5. CMD : The command
        #  6. PRMS : Parameters
        #  7. EOF : 0x0000

        # Two-digit hex strings (without 0x prefix)
        sof = '01'
        reader_type = '03'
        entity = '04'
        eof = '0000'

        result = reader_type + entity + cmd + prms + eof

        length = int(len(result)/2) + 3  # Number of *bytes*, + 3 to include SOF and LENGTH
        length = '%04X' % length  # Convert int to hex
        length = binascii.unhexlify(length)[::-1]  # Reverse hex string to get LSB first
        length = binascii.hexlify(length).decode('ascii')

        result = sof + length + result
        self.write(result.upper())
        response = self.read()
        return self.get_response(response)

    def issue_iso15693_command(self, cmd, flags='', command_code='', data=''):
        return self.issue_evm_command(cmd, flags + command_code + data)

    def flush(self):
        self.sp.readall()

    def write(self, msg):
        self.logger.debug('SEND%3d: ' % (len(msg)/2) + msg[0:10] + colored(msg[10:12], attrs=['underline']) + msg[12:14] + colored(msg[14:], 'green'))
        self.sp.write(msg.encode('ascii'))

    def read(self):
        msg = self.sp.readall()
        self.logger.debug('RETR%3d: ' % (len(msg)/2) + colored(pprint.saferepr(msg).strip("'"), 'cyan'))
        return msg

    def get_response(self, response):
        return re.findall(r'\[(.*?)\]', response.decode('ascii'))

    def close(self):
        self.sp.close()