# -*- coding: utf-8; tab-width: 4; indent-tabs-mode: nil; c-basic-offset: 4 -*-
# vim:fenc=utf-8:et:sw=4:ts=4:sts=4:tw=0
from __future__ import print_function
import logging
import argparse
import yaml
import time
from copy import copy

from rfidgeek import PyRFIDGeek, ISO15693

# You might need to change this:
COM_PORT_NAME = '/dev/tty.SLAB_USBtoUART'

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)
logger.addHandler(ch)

reader = PyRFIDGeek(serial_port=COM_PORT_NAME, debug=True)

reader.set_protocol(ISO15693)

try:

    led_enabled = False
    uids = []
    prev_uids = [[], []]
    while True:
        uids = list(reader.inventory())
        successful_reads = []
        print('%d tags' % len(uids))
        if len(uids) > 0 and not led_enabled:
            reader.enable_led(3)
            led_enabled = True
        elif len(uids) == 0 and led_enabled:
            reader.disable_led(3)
            led_enabled = False
        for uid in uids:

            if uid not in prev_uids[0] and uid not in prev_uids[1]:  # and not uid in prev_uids[2]:
                item = reader.read_danish_model_tag(uid)
                if item['error'] != '':
                    print('error reading tag: ', item['error'])
                else:
                    if item['is_blank']:
                        print(' Found blank tag')

                    elif 'id' in item:
                        print
                        print(' Found new tag, usage type: %s' % item['usage_type'])
                        print(' # Item id: %s (part %d of %d)' % (item['id'],
                                                                  item['partno'],
                                                                  item['nparts']))
                        print('   Country: %s, library: %s' % (item['country'],
                                                               item['library']))
                        if item['crc_ok']:
                            print('   CRC check successful')
                            successful_reads.append(uid)
                        else:
                            print('   CRC check failed')

            # reader.unlock_afi(uid)

        # prev_uids[2] = copy(prev_uids[1])
        prev_uids[1] = copy(prev_uids[0])
        prev_uids[0] = copy(uids)

        time.sleep(1)

finally:
    reader.close()
