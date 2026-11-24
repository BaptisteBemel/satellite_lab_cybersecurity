/* Test-only CFE/OSAL boundary stubs. The tested InitChannel function is unmodified. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef int32_t int32;
typedef uint8_t uint8;
typedef uint16_t uint16;
#define TM_SDLP_SUCCESS 0
#define TM_SDLP_INVALID_POINTER (-1)
#define TM_SDLP_INVALID_LENGTH (-2)
#define CRYPTO_LIB_SUCCESS 0
#define CRYPTO_LIB_ERR_NO_INIT (-10)
#define OS_MAX_API_NAME 64
#define TMTF_PRIHDR_LENGTH 6
#define TMTF_SECHDR_MAX_LENGTH 63
#define TMTF_OCF_LENGTH 4
#define TMTF_ERR_CTRL_FIELD_LENGTH 2
#define TMTF_NO_FIRST_HDR_PTR 0x7ff
#define KRED ""
#define RESET ""
#define CFE_EVS_SendEvent(...) ((void)0)
#define CFE_PSP_MemSet memset
typedef struct { uint8 byte; } TMTF_PriHdr_t;
typedef struct { uint16 frameLength, scId; bool hasErrCtrl; } TM_SDLP_GlobalConfig_t;
typedef struct {
    uint16 secHdrLength;
    bool fshFlag, ocfFlag;
    uint8 vcId;
    uint16 overflowSize;
    uint8 dataType;
} TM_SDLP_ChannelConfig_t;
typedef struct {
    uint16 dataFieldLength, dataFieldOffset, ocfOffset, freeOctets;
    uint16 currentDataOffset, errCtrlOffset;
    TM_SDLP_GlobalConfig_t *globConfig;
    TM_SDLP_ChannelConfig_t *chnlConfig;
    TMTF_PriHdr_t *frame;
    bool isFirstHdrPtrSet, isReady, isInitialized;
    int mutexId;
    struct {
        uint16 buffSize, freeOctets, partialOctets;
        uint8 *dataStart, *dataEnd, *buffer;
    } overflowInfo;
} TM_SDLP_FrameInfo_t;
typedef struct { uint8 shivf_len, shsnf_len, shplf_len; } SecurityAssociation_t;
int test_sa_calls;
SecurityAssociation_t test_sa = {12, 0, 0};
int test_get_sa(int version, uint16 scid, uint16 vcid, int mapid, SecurityAssociation_t **sa)
{
    (void)version; (void)scid; (void)vcid; (void)mapid;
    *sa = &test_sa;
    test_sa_calls++;
    return 0;
}
struct test_sa_interface {
    int (*sa_get_operational_sa_from_gvcid)(int, uint16, uint16, int, SecurityAssociation_t **);
} test_sa_if = {test_get_sa}, *sa_if = &test_sa_if;
int Crypto_Get_Security_Header_Length(SecurityAssociation_t *sa) { (void)sa; return 14; }
int Crypto_Get_Security_Trailer_Length(SecurityAssociation_t *sa) { (void)sa; return 16; }
#define TEST_SETTER(name) void name(TMTF_PriHdr_t *frame, uint16 value) { (void)frame; (void)value; }
TEST_SETTER(TMTF_SetScId)
TEST_SETTER(TMTF_SetVcId)
TEST_SETTER(TMTF_SetOcfFlag)
TEST_SETTER(TMTF_SetSecHdrFlag)
TEST_SETTER(TMTF_SetSecHdrLength)
TEST_SETTER(TMTF_SetSyncFlag)
TEST_SETTER(TMTF_SetPacketOrderFlag)
TEST_SETTER(TMTF_SetSegLengthId)
TEST_SETTER(TMTF_SetFirstHdrPtr)
uint16 TMTF_GetGlobalVcId(TMTF_PriHdr_t *frame) { (void)frame; return 1; }
void OS_MutSemCreate(int *id, const char *name, int flags) { *id = 1; (void)name; (void)flags; }
