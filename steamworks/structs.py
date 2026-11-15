import sys
from ctypes import *

# Steam SDK callback/detail structs use platform-specific pragma packing:
# - Linux / macOS：VALVE_CALLBACK_PACK_SMALL → #pragma pack(push, 4)
# - Windows：VALVE_CALLBACK_PACK_LARGE → #pragma pack(push, 8)
# Python ctypes defaults to natural alignment (uint64 -> 8 bytes), matching Windows;
# Linux/macOS require explicit _pack_ = 4 or uint64 fields are misread.
_STEAM_CALLBACK_PACK = 8 if sys.platform == 'win32' else 4


class FindLeaderboardResult_t(Structure):
    """ Represents the STEAMWORKS LeaderboardFindResult_t call result type """
    # u64 at offset 0: both pack modes yield the same layout; declare explicitly only for symmetry with other
    # callback structs and SDK VALVE_CALLBACK_PACK_SMALL/LARGE.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("leaderboardHandle", c_uint64),
        ("leaderboardFound", c_uint32)
    ]


class CreateItemResult_t(Structure):
    # int + u64 + bool: pack=8 puts u64@8 with 4 bytes padding; pack=4 puts u64@4.
    # Linux/macOS need explicit _pack_=4 to read publishedFileId correctly.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("result", c_int),
        ("publishedFileId", c_uint64),
        ("userNeedsToAcceptWorkshopLegalAgreement", c_bool)
    ]


class SubmitItemUpdateResult_t(Structure):
    # int + bool + u64: padding after bool places u64 at offset 8 for both pack=4/8,
    # coincidentally matching the current layout. Explicit _pack_ maintains symmetry with other callback structs,
    # preventing regressions after future field reordering.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("result", c_int),
        ("userNeedsToAcceptWorkshopLegalAgreement", c_bool),
        ("publishedFileId", c_uint64)
    ]


class ItemInstalled_t(Structure):
    # u32 + u64: pack=8 puts u64@8; pack=4 puts u64@4.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("appId", c_uint32),
        ("publishedFileId", c_uint64)
    ]


class SubscriptionResult(Structure):
    # i32 + u64: pack=8 puts u64@8; pack=4 puts u64@4.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("result", c_int32),
        ("publishedFileId", c_uint64)
    ]


class SteamUGCQueryCompleted_t(Structure):
    # u64 at offset 0: current layout matches both pack modes; declare explicitly only for symmetry.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("handle", c_uint64),
        ("result", c_int),
        ("numResultsReturned", c_uint32),
        ("totalMatchingResults", c_uint32),
        ("cachedData", c_bool)
    ]


class SteamUGCDetails_t(Structure):
    # On Linux/macOS, Steam SDK uses VALVE_CALLBACK_PACK_SMALL (#pragma
    # pack(push, 4)), aligning uint64 to 4 bytes. Python ctypes defaults to
    # natural 8-byte alignment, shifting fields such as m_ulSteamIDOwner by 4
    # bytes. steamIDOwner's low 32 bits then always become 0x01100001, the Public/Individual/
    # Desktop universe|type|instance bits. Passing that fake Steam ID to
    # GetFriendPersonaName makes Steam return a varying sentinel
    # string (observed "ZeroGravity"), displaying the same wrong author on all
    # Workshop items. Windows SDK uses VALVE_CALLBACK_PACK_LARGE (pack=8),
    # matching ctypes defaults, so explicitly set _pack_=4 only for SMALL platforms,
    # retaining default 8-byte alignment on Windows.
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
    # u32 + u64 + bool: pack=8 puts u64@8; pack=4 puts u64@4.
    _pack_ = _STEAM_CALLBACK_PACK
    _fields_ = [
        ("appId", c_uint32),
        ("orderId", c_uint64),
        ("authorized", c_bool)
    ]
