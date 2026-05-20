import sys
from ctypes import *

# Steam SDK 的 callback / 详情 struct 在不同平台用不同 pragma pack：
# - Linux / macOS：VALVE_CALLBACK_PACK_SMALL → #pragma pack(push, 4)
# - Windows：VALVE_CALLBACK_PACK_LARGE → #pragma pack(push, 8)
# Python ctypes 默认按自然对齐（uint64 → 8 字节），与 Windows 一致；
# 在 Linux/macOS 上需要显式 _pack_ = 4，否则 uint64 字段会被读偏。
_STEAM_CALLBACK_PACK = 8 if sys.platform == 'win32' else 4


class FindLeaderboardResult_t(Structure):
    """ Represents the STEAMWORKS LeaderboardFindResult_t call result type """
    _fields_ = [
        ("leaderboardHandle", c_uint64),
        ("leaderboardFound", c_uint32)
    ]


class CreateItemResult_t(Structure):
    _fields_ = [
        ("result", c_int),
        ("publishedFileId", c_uint64),
        ("userNeedsToAcceptWorkshopLegalAgreement", c_bool)
    ]


class SubmitItemUpdateResult_t(Structure):
    _fields_ = [
        ("result", c_int),
        ("userNeedsToAcceptWorkshopLegalAgreement", c_bool),
        ("publishedFileId", c_uint64)
    ]


class ItemInstalled_t(Structure):
    _fields_ = [
        ("appId", c_uint32),
        ("publishedFileId", c_uint64)
    ]


class SubscriptionResult(Structure):
    _fields_ = [
        ("result", c_int32),
        ("publishedFileId", c_uint64)
    ]


class SteamUGCQueryCompleted_t(Structure):
    _fields_ = [
        ("handle", c_uint64),
        ("result", c_int),
        ("numResultsReturned", c_uint32),
        ("totalMatchingResults", c_uint32),
        ("cachedData", c_bool)
    ]


class SteamUGCDetails_t(Structure):
    # Linux/macOS 下 Steam SDK 用 VALVE_CALLBACK_PACK_SMALL（#pragma
    # pack(push, 4)），uint64 字段按 4 字节对齐。Python ctypes 默认走
    # 8 字节自然对齐，于是 m_ulSteamIDOwner 等 uint64 字段被读偏 4
    # 字节，steamIDOwner 低 32 位永远是 0x01100001（Public/Individual/
    # Desktop 的 universe|type|instance 位），把伪 Steam ID 喂给
    # GetFriendPersonaName 时 Steam 客户端会返回一个不固定的 sentinel
    # 字符串（实测返回 "ZeroGravity"），让所有创意工坊条目都显示成同一
    # 错误作者。Windows 下 SDK 用 VALVE_CALLBACK_PACK_LARGE（pack=8），
    # 与 ctypes 默认值一致，因此 _pack_ 仅按 SMALL 平台显式设置 4，
    # Windows 维持 ctypes 默认 8 字节对齐。
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("publishedFileId", c_uint64),
        ("result", c_int),
        ("fileType", c_int),
        ("creatorAppID", c_uint32),
        ("consumerAppID", c_uint32),
        ("title", c_char * 129),
        ("description", c_char * 8000),
        ("steamIDOwner", c_uint64),
        ("timeCreated", c_uint32),
        ("timeUpdated", c_uint32),
        ("timeAddedToUserList", c_uint32),
        ("visibility", c_int),
        ("banned", c_bool),
        ("acceptedForUse", c_bool),
        ("tagsTruncated", c_bool),
        ("tags", c_char * 1025),
        ("file", c_uint64),
        ("previewFile", c_uint64),
        ("fileName", c_char * 260),
        ("fileSize", c_uint32),
        ("previewFileSize", c_uint32),
        ("URL", c_char * 256),
        ("votesUp", c_uint32),
        ("votesDown", c_uint32),
        ("score", c_float),
        ("numChildren", c_uint32),
    ]


class MicroTxnAuthorizationResponse_t(Structure):
    _fields_ = [
        ("appId", c_uint32),
        ("orderId", c_uint64),
        ("authorized", c_bool)
    ]
