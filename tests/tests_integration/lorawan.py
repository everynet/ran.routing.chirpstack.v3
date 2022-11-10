import os
from typing import Union

import pylorawan
from pylorawan.message import MType
from pylorawan.message import MACPayload, JoinAccept, JoinRequest, Proprietary


def generate_data_message(
    app_s_key, nwk_s_key, dev_addr, frm_payload, confirmed=False, f_cnt=1, f_port=1, ack=False, f_opts=b"", f_opts_len=0
):
    mtype = pylorawan.message.MType.UnconfirmedDataUp
    if confirmed:
        mtype = pylorawan.message.MType.ConfirmedDataUp

    mhdr = pylorawan.message.MHDR(mtype=mtype, major=0)
    direction = 0
    encrypted_frm_payload = pylorawan.common.encrypt_frm_payload(
        frm_payload, bytes.fromhex(app_s_key), int(dev_addr, 16), f_cnt, direction
    )

    f_ctrl = pylorawan.message.FCtrlUplink(adr=True, adr_ack_req=False, ack=ack, class_b=False, f_opts_len=f_opts_len)

    fhdr = pylorawan.message.FHDRUplink(dev_addr=int(dev_addr, 16), f_ctrl=f_ctrl, f_cnt=f_cnt, f_opts=f_opts)

    mac_payload = pylorawan.message.MACPayloadUplink(fhdr=fhdr, f_port=f_port, frm_payload=encrypted_frm_payload)
    mic = pylorawan.common.generate_mic_mac_payload(mhdr, mac_payload, bytes.fromhex(nwk_s_key))

    return pylorawan.message.PHYPayload(mhdr=mhdr, payload=mac_payload, mic=mic)


def generate_join_request(app_key, app_eui, dev_eui, dev_nonce=None):
    if dev_nonce is None:
        dev_nonce = int.from_bytes(os.urandom(2), "little")

    mtype = pylorawan.message.MType.JoinRequest
    mhdr = pylorawan.message.MHDR(mtype=mtype, major=0)

    join_request = pylorawan.message.JoinRequest(
        app_eui=int(app_eui, 16), dev_eui=int(dev_eui, 16), dev_nonce=dev_nonce
    )
    mic = pylorawan.common.generate_mic_join_request(mhdr, join_request, bytes.fromhex(app_key))

    return pylorawan.message.PHYPayload(mhdr=mhdr, payload=join_request, mic=mic)
