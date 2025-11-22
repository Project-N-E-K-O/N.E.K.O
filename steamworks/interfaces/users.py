from ctypes import *
from enum import Enum

import steamworks.util 		as util
from steamworks.enums 		import *
from steamworks.structs 	import *
from steamworks.exceptions 	import *


class SteamUsers(object):
    def __init__(self, steam: object):
        self.steam = steam
        if not self.steam.loaded():
            raise SteamNotLoadedException('STEAMWORKS not yet loaded')


    def GetSteamID(self) -> int:
        """Get user's Steam ID.

        :return: int
        """
        return self.steam.GetSteamID()


    def LoggedOn(self) -> bool:
        """Check, true/false, if user is logged into Steam currently

        :return: bool
        """
        return self.steam.LoggedOn()


    def GetPlayerSteamLevel(self) -> int:
        """Get the user's Steam level.

        :return: int
        """
        return self.steam.GetPlayerSteamLevel()


    def GetGameBadgeLevel(self, series: int, foil: int) -> int:
        """Trading Card badges data access, if you only have one set of cards, the series will be 1.
        # The user has can have two different badges for a series; the regular (max level 5) and the foil (max level 1).

        :param series: int
        :param foil: int
        :return: int
        """
        return self.steam.GetGameBadgeLevel(series, foil)
		
    def GetAuthSessionTicket(self) -> str:
        """Retrieves an authentication ticket. Immediately usable in AuthenticateUserTicket.
		
		:return: str
		"""
        buffer = create_string_buffer(1024)
        length = self.steam.GetAuthSessionTicket(buffer)
        return buffer[0:length].hex().upper()