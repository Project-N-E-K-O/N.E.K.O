from ctypes import *
from enum import Enum

import steamworks.util 		as util
from steamworks.enums 		import *
from steamworks.structs 	import *
from steamworks.exceptions 	import *


class SteamMicroTxn(object):
    _MicroTxnAuthorizationResponse_t = CFUNCTYPE(None, MicroTxnAuthorizationResponse_t)
    _MicroTxnAuthorizationResponse = None

    def __init__(self, steam: object):
        self.steam = steam
        if not self.steam.loaded():
            raise SteamNotLoadedException('STEAMWORKS not yet loaded')

    def SetAuthorizationResponseCallback(self, callback: object) -> bool:
        """Set callback for when Steam informs about the consent flow result

        :param callback: callable
        :return: bool
        """
        self._MicroTxnAuthorizationResponse = SteamMicroTxn._MicroTxnAuthorizationResponse_t(callback)
        self.steam.MicroTxn_SetAuthorizationResponseCallback(self._MicroTxnAuthorizationResponse)
        return True
