import hashlib
import os
import uuid
from typing import Callable

import pylorawan
import pytest

from lib.traffic.models import LoRaModulation, Uplink, UpstreamRadio

UplinkMaker = Callable[[pylorawan.message.PHYPayload], Uplink]


@pytest.fixture
def make_uplink(region_params) -> UplinkMaker:
    uplink_params = region_params["uplink"]

    def _make_uplink(message: pylorawan.message.PHYPayload) -> Uplink:
        lora_modulation = LoRaModulation(
            spreading=uplink_params["spreading"],
            bandwidth=uplink_params["bandwidth"],
        )
        radio_params = UpstreamRadio(
            frequency=uplink_params["frequency"],
            rssi=-120,
            snr=1.0,
            lora=lora_modulation,
        )
        uplink = Uplink(
            uplink_id=str(uuid.UUID(bytes=hashlib.md5(message.generate()).digest())),
            payload=message,
            used_mic=int.from_bytes(message.mic, byteorder="little"),  # TODO: ensure byte order
            radio=radio_params,
        )
        return uplink

    return _make_uplink


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


def generate_downlink(dev_addr: str, app_s_key: str, nwk_s_key: str, f_cnt: int = 0, frm_payload: bytes = b""):
    f_opts_len = 0
    f_opts = b""
    f_port = 0
    adr = False
    confirmed = False
    f_pending = False

    encrypted_frm_payload = pylorawan.common.encrypt_frm_payload(
        frm_payload,
        bytes.fromhex(app_s_key),
        int(dev_addr, 16),
        f_cnt,
        1,
    )

    mtype = pylorawan.message.MType.UnconfirmedDataDown
    mhdr = pylorawan.message.MHDR(mtype=mtype, major=0)

    f_ctrl = pylorawan.message.FCtrlDownlink(
        adr=adr,
        ack=confirmed,
        f_pending=f_pending,
        f_opts_len=f_opts_len,
    )
    fhdr = pylorawan.message.FHDRDownlink(dev_addr=int(dev_addr, 16), f_ctrl=f_ctrl, f_cnt=f_cnt, f_opts=f_opts)
    mac_payload = pylorawan.message.MACPayloadDownlink(fhdr=fhdr, f_port=f_port, frm_payload=encrypted_frm_payload)

    mic = pylorawan.common.generate_mic_mac_payload(mhdr, mac_payload, bytes.fromhex(nwk_s_key))

    return pylorawan.message.PHYPayload(mhdr=mhdr, payload=mac_payload, mic=mic)
