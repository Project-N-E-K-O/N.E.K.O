from steamworks.enums 		import *
from steamworks.structs 	import *
from steamworks.exceptions 	import *


class SteamInput:
    def __init__(self, steam: object):
        self.steam = steam
        if not self.steam.loaded():
            raise SteamNotLoadedException('STEAMWORKS not yet loaded')

    def Init(self, explicitlyCallRunFrame=False):
        return self.steam.ControllerInit(explicitlyCallRunFrame)

    def SetInputActionManifestFilePath(self, path):
        return self.steam.SetInputActionManifestFilePath(path.encode())

    def RunFrame(self):
        return self.steam.RunFrame()

    def GetConnectedControllers(self) -> list[int]:
        controllers_array = self.steam.GetConnectedControllers()
        return [controller for i in range(16) if (controller := controllers_array[i]) != 0]

    def GetControllerForGamepadIndex(self, index: int) -> int:
        return self.steam.GetControllerForGamepadIndex(index)

    def GetActionSetHandle(self, name: str) -> int:
        return self.steam.GetActionSetHandle(name.encode('ascii'))

    def ActivateActionSet(self, controller, action_set):
        return self.steam.ActivateActionSet(controller, action_set)

    def GetAnalogActionHandle(self, name: str) -> int:
        return self.steam.GetAnalogActionHandle(name.encode('ascii'))

    def GetAnalogActionData(self, controller, analog_action):
        return self.steam.GetAnalogActionData(controller, analog_action)

    def GetDigitalActionHandle(self, name: str) -> int:
        return self.steam.GetDigitalActionHandle(name.encode('ascii'))

    def GetDigitalActionData(self, controller, digital_action):
        return self.steam.GetDigitalActionData(controller, digital_action)