from ctypes import *


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
