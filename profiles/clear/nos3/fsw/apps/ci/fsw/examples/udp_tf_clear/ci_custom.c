/******************************************************************************/
/** \file  ci_custom.c
*
*   Copyright 2017 United States Government as represented by the Administrator
*   of the National Aeronautics and Space Administration.  No copyright is
*   claimed in the United States under Title 17, U.S. Code.
*   All Other Rights Reserved.
*
*   \author Guy de Carufel (Odyssey Space Research), NASA, JSC, ER6
*
*   \brief Custom CI transport for clear CCSDS TC Transfer Frames over UDP
*
*   \par Purpose
*     This transport is the non-SDLS baseline used by the laboratory.  It keeps
*     the UDP transport of the original CI example, but the UDP payload is now a
*     complete CCSDS TC Transfer Frame (TCTF), not a bare cFS command packet.
*     The frame is validated and decoded with the TCTF functions already
*     provided by cFS IO_LIB.
*
*   \par Expected wire format
*     - One UDP datagram contains exactly one complete TCTF.
*     - The TCTF contains a 5-byte primary header and a 1-byte segment header.
*     - The segment header identifies MAP 1 and marks the payload unsegmented.
*     - The payload contains exactly one complete cFS/CCSDS command packet.
*     - No CLTU, CLTU randomization, COP-1, FECF, or SDLS field is used.
*
*   \par Mission link parameters
*     - Transfer Frame Version Number: 0
*     - Link-layer Spacecraft ID: 3
*     - Virtual Channel ID: 0
*     - MAP ID: 1
*
*     These values describe the TC link and must match the Yamcs
*     UdpTcFrameLink configuration.  The link-layer Spacecraft ID 3 is distinct
*     from the cFS mission Spacecraft ID 42 used elsewhere by cFS.
*
*   \par API Functions Defined
*     - CI_CustomInit() - Initialize the UDP transport and expected TCTF channel
*     - CI_CustomAppCmds() - Process custom CI application commands
*     - CI_CustomEnableTO() - Send a command to TO to enable the downlink
*     - CI_CustomCleanup() - Close the UDP socket
*     - CI_CustomMain() - Receive complete TCTFs in the CI child task
*     - CI_CustomGateCmds() - Process CI gate commands
*
*   \par Private Functions Defined
*     - CI_CustomProcessTctf() - Validate, decapsulate, and dispatch one TCTF
*
*   \par Limitations and deliberate baseline choices
*     - One and only one Space Packet is accepted per TCTF.
*     - Packet segmentation across several TCTFs is rejected.
*     - COP-1 control and retransmission processing is not implemented.
*     - A Frame Error Control Field is not present and is not checked.
*     - CryptoLib is deliberately not called because this is the clear baseline.
*     - The embedded cFS command checksum is not validated here.  This preserves
*       the behavior of the original NOS3 UDP CI example and must be documented
*       as a baseline security limitation.  It can be enabled in a later,
*       separate hardening step without changing TCTF decapsulation.
*     - All configurable values are defined in ci_platform_cfg.h.
*     - ciMutex must be used whenever shared g_CI_AppData fields are accessed.
*
*   \par Modification History
*     - 2015-01-09 | Guy de Carufel | Code Started
*     - 2015-06-02 | Guy de Carufel | Revised for new UDP API
*     - 2026-09-17 | Laboratory project | Added clear TCTF reception via IO_LIB
*******************************************************************************/

/*
** Include Files
*/
#include "cfe.h"
#include "network_includes.h"
#include "trans_udp.h"

/*
 * IO_LIB already implements the CCSDS TC Transfer Frame field accessors,
 * validation, payload-length calculation, and payload-copy operation used
 * below.  No mission-specific TCTF parser is duplicated in this transport.
 */
#include "tctf.h"

#include "ci_app.h"
#include "ci_platform_cfg.h"

/*
** Local Defines
*/

/*
 * A CCSDS Space Packet primary header is six octets.  CI must not ask the cFE
 * message API to inspect a payload shorter than that header.
 */
#define CI_CUSTOM_SPACE_PACKET_PRIHDR_SIZE 6U

/*
** Local Structure Declarations
*/
typedef struct
{
    /* UDP socket on which the radio simulator delivers uplink frames. */
    IO_TransUdp_t udp;

    /* Existing CI/TO command used to enable telemetry output. */
    TO_EnableOutputCmd_t toEnableCmd;

    /*
     * Raw UDP payload.  It contains the complete clear TCTF, including its
     * primary header and segment header.
     */
    uint8 frameBuffer[CI_CUSTOM_BUFFER_SIZE];

    /*
     * Decapsulated TCTF payload.  A separate buffer avoids publishing the
     * surrounding transfer-frame header on the cFS Software Bus.
     */
    uint8 packetBuffer[CI_CUSTOM_BUFFER_SIZE];

    /*
     * Managed parameters describing the only TCTF channel accepted by this
     * baseline receiver.  TCTF_IsValidTf() compares incoming frame identifiers
     * against this structure.
     */
    TCTF_ChannelService_t channelService;
} CI_CustomData_t;

/*
** External Global Variables
*/

/* NOTE: Make use of ciMutex when accessing data shared by the main task. */
extern CI_AppData_t g_CI_AppData;

/*
** Local Variables
*/
static CI_CustomData_t g_CI_CustomData;

/*
** Local Function Prototypes
*/
static void CI_CustomProcessTctf(int32 frameSize);

/*******************************************************************************
** Custom Application Functions (Executed by Main Task)
*******************************************************************************/

/******************************************************************************/
/** \brief Initialize the UDP socket and the expected clear-TCTF channel
 *
 *  \return CI_SUCCESS when the socket and child task are created, CI_ERROR
 *          otherwise.
 ******************************************************************************/
int32 CI_CustomInit(void)
{
    int32 iStatus = CI_ERROR;
    uint32 taskId = 0;
    IO_TransUdpConfig_t config;

    /*
     * Expected uplink service:
     *
     * TCTF_SERVICE_MAPP
     *     Yamcs is configured with a positive mapId, so it emits the one-byte
     *     segment header and uses the MAP Packet service.
     * PacketVersionNumber = 0
     *     CCSDS TC Transfer Frame Version Number used by this mission profile.
     * SpacecraftId = 3, VirtualChannelId = 0, MapId = 1
     *     Link identifiers shared with the Yamcs uplink configuration.
     * HasSegHdr = true
     *     Required because MAP service is enabled.
     * HasFrameErrCtl = false
     *     Matches Yamcs errorDetection: NONE for the clear baseline.
     */
    TCTF_ChannelService_t channelConfig = {
        .Service             = TCTF_SERVICE_MAPP,
        .PacketVersionNumber = 0,
        .SpacecraftId        = CI_CUSTOM_TF_SCID,
        .VirtualChannelId    = CI_CUSTOM_TF_VCID,
        .MapId               = CI_CUSTOM_TF_MAPID,
        .HasSegHdr           = (CI_CUSTOM_TF_HAS_SEG_HDR != 0),
        .HasFrameErrCtl      = (CI_CUSTOM_TF_HAS_FECF != 0)
    };

    /*
     * Use -1 as the explicit invalid-socket state.  This makes the later check
     * in CI_CustomMain() meaningful even if UDP initialization fails.
     */
    g_CI_CustomData.udp.sockId = -1;

    /*
     * Configure CI to listen on every local interface at the mission-defined
     * internal port.  For this profile CI_CUSTOM_UDP_PORT is 5010; the radio
     * simulator must therefore forward its external port 8010 to nos-fsw:5010.
     */
    CFE_PSP_MemSet((void *)&config, 0x0, sizeof(IO_TransUdpConfig_t));
    strncpy(config.cAddr, CI_CUSTOM_UDP_ADDR, CI_CUSTOM_MAX_IP_STRING_SIZE);
    config.usPort     = CI_CUSTOM_UDP_PORT;
    config.timeoutRcv = CI_CUSTOM_UDP_TIMEOUT;

    if (IO_TransUdpInit(&config, &g_CI_CustomData.udp) < 0)
    {
        goto end_of_function;
    }

    /*
     * Keep the managed parameters in persistent application storage because
     * the receive child task uses them after CI_CustomInit() has returned.
     */
    CFE_PSP_MemCpy((void *)&g_CI_CustomData.channelService,
                   (void *)&channelConfig,
                   sizeof(TCTF_ChannelService_t));

    /*
     * The child task blocks on UDP reception and performs TCTF decapsulation;
     * the normal CI main task remains responsible for CI application commands.
     */
    iStatus = CFE_ES_CreateChildTask(&taskId,
                                     "CI Custom Main Task",
                                     CI_CustomMain,
                                     CI_CUSTOM_TASK_STACK_PTR,
                                     CI_CUSTOM_TASK_STACK_SIZE,
                                     CI_CUSTOM_TASK_PRIO,
                                     0);

end_of_function:
    return iStatus;
}

/******************************************************************************/
/** \brief Process a transport-specific CI application command
 *
 *  No additional application command is defined for this transport.  The
 *  function is retained because it is part of the CI custom-layer interface.
 ******************************************************************************/
int32 CI_CustomAppCmds(CFE_MSG_Message_t *cmdMsgPtr)
{
    int32 iStatus = CI_SUCCESS;
    CFE_MSG_FcnCode_t uiCmdCode = 0;

    CFE_MSG_GetFcnCode(cmdMsgPtr, &uiCmdCode);

    switch (uiCmdCode)
    {
        /*
         * Add mission-specific transport commands here if they are required in
         * the future.  None is needed for the clear-TCTF baseline.
         */
        default:
            iStatus = CI_ERROR;
            break;
    }

    return iStatus;
}

/******************************************************************************/
/** \brief Forward CI_ENABLE_TO_CC to TO so telemetry output is enabled
 ******************************************************************************/
void CI_CustomEnableTO(CFE_MSG_Message_t *cmdMsgPtr)
{
    /* Preserve the parameters carried by the incoming CI command. */
    CFE_PSP_MemCpy((void *)&g_CI_CustomData.toEnableCmd,
                   (void *)cmdMsgPtr,
                   sizeof(TO_EnableOutputCmd_t));

    /* Rewrite the copied message as a valid TO_ENABLE_OUTPUT command. */
    CFE_MSG_Init(CFE_MSG_PTR(g_CI_CustomData.toEnableCmd.ucCmdHeader),
                 CFE_SB_ValueToMsgId(TO_APP_CMD_MID),
                 sizeof(TO_EnableOutputCmd_t));
    CFE_MSG_SetFcnCode((CFE_MSG_Message_t *)&g_CI_CustomData.toEnableCmd,
                       TO_ENABLE_OUTPUT_CC);
    CFE_MSG_GenerateChecksum((CFE_MSG_Message_t *)&g_CI_CustomData.toEnableCmd);

    /* Publish the command on the cFS Software Bus for TO. */
    CFE_SB_TransmitMsg((CFE_MSG_Message_t *)&g_CI_CustomData.toEnableCmd, true);
}

/******************************************************************************/
/** \brief Close the UDP socket owned by this CI transport
 ******************************************************************************/
void CI_CustomCleanup(void)
{
    IO_TransUdpCloseSocket(&g_CI_CustomData.udp);
}

/*******************************************************************************
** Custom Functions (Executed by Custom Child Task)
*******************************************************************************/

/******************************************************************************/
/** \brief Validate and decapsulate one clear TCTF
 *
 *  The function applies checks in an order that prevents IO_LIB or cFE from
 *  reading fields that are not present:
 *
 *  1. Check the received UDP length before reading the TCTF header.
 *  2. Require the length declared by the TCTF to equal the UDP payload length.
 *  3. Validate TFVN, SCID, VCID, and MAP ID with IO_LIB.
 *  4. Reject COP-1 control frames and segmented payloads.
 *  5. Copy the payload with IO_LIB into a separate packet buffer.
 *  6. Require exactly one complete Space Packet in that payload.
 *  7. Publish the packet to the Software Bus, or process a CI gate command.
 *
 *  \param[in] frameSize Number of octets returned by the UDP receive function.
 ******************************************************************************/
static void CI_CustomProcessTctf(int32 frameSize)
{
    TCTF_Hdr_t *tf;
    CFE_MSG_Message_t *sbMsg;
    CFE_MSG_Size_t msgSize = 0;
    CFE_SB_MsgId_t msgId;
    uint16 tfLength;
    uint16 payloadLength;
    uint16 copiedLength;

    /*
     * MAP service requires both the five-byte primary header and the one-byte
     * segment header.  The configured maximum is 1024 octets.  The receive
     * buffer is intentionally slightly larger, allowing oversized datagrams to
     * be observed and rejected instead of being accepted as valid frames.
     */
    if ((frameSize < (int32)(TCTF_PRIHDR_SIZE + TCTF_SEGHDR_SIZE)) ||
        (frameSize > (int32)CI_CUSTOM_TF_MAX_SIZE) ||
        (frameSize > (int32)sizeof(g_CI_CustomData.frameBuffer)))
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: Invalid clear TCTF size: %d",
                          (int)frameSize);
        return;
    }

    /* The size check above guarantees that the complete TCTF header exists. */
    tf = (TCTF_Hdr_t *)&g_CI_CustomData.frameBuffer[0];

    /*
     * The CCSDS length field is authoritative for the frame.  Requiring exact
     * equality also rejects truncated datagrams and datagrams with trailing
     * bytes after the declared TCTF.
     */
    tfLength = TCTF_GetLength(tf);
    if (tfLength != (uint16)frameSize)
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: TCTF declared length %u differs from UDP length %d",
                          (unsigned int)tfLength,
                          (int)frameSize);
        return;
    }

    /*
     * IO_LIB compares the incoming TFVN/SCID/VCID/MAP ID with channelService.
     * In this profile the accepted tuple is (0, 3, 0, 1).
     */
    if (!TCTF_IsValidTf(tf, &g_CI_CustomData.channelService))
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: Invalid clear TCTF: SCID=%u VCID=%u MAPID=%u",
                          (unsigned int)TCTF_GetScId(tf),
                          (unsigned int)TCTF_GetVcId(tf),
                          (unsigned int)TCTF_GetSegHdrMapId(tf));
        return;
    }

    /*
     * BC control frames belong to COP-1.  This baseline does not instantiate a
     * COP-1/FARM state machine, so accepting such a frame would be misleading.
     */
    if (TCTF_GetCtlCmdFlag(tf) != TCTF_DATA_FRAME)
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: COP-1 control frame rejected in clear non-COP-1 mode");
        return;
    }

    /*
     * Yamcs mapId=1 emits sequence flags 0b11, meaning one complete unsegmented
     * service data unit.  Reassembly is intentionally outside this baseline.
     */
    if (TCTF_GetSegHdrSeqFlags(tf) != TCTF_NO_SEGMENTATION)
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: Segmented TCTF rejected");
        return;
    }

    /*
     * IO_LIB subtracts the configured primary header, segment header, and FECF
     * sizes from the validated frame length.  HasFrameErrCtl is false here, so
     * no two-byte FECF is removed.
     */
    payloadLength = TCTF_GetPayloadLength(tf, &g_CI_CustomData.channelService);

    /*
     * A payload shorter than a Space Packet primary header cannot safely be
     * inspected by CFE_MSG_GetSize().  The upper bound protects packetBuffer.
     */
    if ((payloadLength < CI_CUSTOM_SPACE_PACKET_PRIHDR_SIZE) ||
        (payloadLength > sizeof(g_CI_CustomData.packetBuffer)))
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: Invalid TCTF payload length: %u",
                          (unsigned int)payloadLength);
        return;
    }

    /* Copy only the TCTF service data unit; do not publish TCTF headers to SB. */
    copiedLength = TCTF_CopyData(&g_CI_CustomData.packetBuffer[0],
                                 tf,
                                 &g_CI_CustomData.channelService);

    if (copiedLength != payloadLength)
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: TCTF payload copy failed");
        return;
    }

    sbMsg = (CFE_MSG_Message_t *)&g_CI_CustomData.packetBuffer[0];

    /*
     * The Space Packet's own length must consume the full TCTF payload.  This
     * implements the chosen one-packet-per-frame rule and rejects both a
     * truncated packet and extra bytes/multiple packets in the same TCTF.
     */
    CFE_MSG_GetSize(sbMsg, &msgSize);
    if (msgSize != payloadLength)
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: Space Packet size %u differs from TCTF payload %u",
                          (unsigned int)msgSize,
                          (unsigned int)payloadLength);
        return;
    }

    /*
     * Deliberate baseline limitation: CFE_SB_ValidateChecksum() is not called,
     * matching the original NOS3 UDP example.  Thus the TCTF structure and
     * destination are checked, but this layer provides no command-integrity or
     * authenticity guarantee.  SDLS will be evaluated in a separate profile.
     */
    CFE_MSG_GetMsgId(sbMsg, &msgId);

    /* Gate commands are consumed by CI; ordinary commands enter Software Bus. */
    if (CFE_SB_MsgIdToValue(msgId) == CI_GATE_CMD_MID)
    {
        CI_CustomGateCmds(sbMsg);
    }
    else
    {
        CFE_SB_TransmitMsg(sbMsg, true);
    }
}

/******************************************************************************/
/** \brief Receive clear TCTFs from UDP and pass each one to the decoder
 *
 *  IO_TransUdpRcvTimeout() returns the number of bytes copied from one UDP
 *  datagram.  UDP preserves datagram boundaries, so one call corresponds to
 *  one candidate TCTF in this laboratory profile.
 ******************************************************************************/
void CI_CustomMain(void)
{
    int32 size = 0;

    /* Do not enter the receive loop if initialization did not create a socket. */
    if (g_CI_CustomData.udp.sockId < 0)
    {
        CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                          CFE_EVS_EventType_ERROR,
                          "CI: Socket ID not set. Check init. Quitting CI_CustomMain.");
        return;
    }

    /*
     * Continue until the transport reports a negative receive error.  A zero
     * return carries no frame and simply causes the loop to wait again.
     */
    while (size >= 0)
    {
        size = IO_TransUdpRcvTimeout(&g_CI_CustomData.udp,
                                     &g_CI_CustomData.frameBuffer[0],
                                     CI_CUSTOM_BUFFER_SIZE,
                                     IO_TRANS_PEND_FOREVER);

        if (size > 0)
        {
            CI_CustomProcessTctf(size);
        }
    }

    CFE_EVS_SendEvent(CI_CUSTOM_ERR_EID,
                      CFE_EVS_EventType_ERROR,
                      "CI: Error occurred on socket read. Quitting CI_CustomMain.");
}

/******************************************************************************/
/** \brief Process a CI gate command extracted from a valid clear TCTF
 *
 *  No mission-specific gate command is defined here.  Retaining this function
 *  preserves the routing behavior of the original CI UDP transport.
 ******************************************************************************/
void CI_CustomGateCmds(CFE_MSG_Message_t *cmdMsgPtr)
{
    CFE_MSG_FcnCode_t uiCmdCode = 0;

    CFE_MSG_GetFcnCode(cmdMsgPtr, &uiCmdCode);

    switch (uiCmdCode)
    {
        /* Add a supported gate command case here if the mission needs one. */
        default:
            CI_IncrHkCounter(&g_CI_AppData.HkTlm.usCmdErrCnt);
            CFE_EVS_SendEvent(CI_CMD_ERR_EID,
                              CFE_EVS_EventType_ERROR,
                              "CI: Recvd invalid Gate cmd (%d)",
                              uiCmdCode);
            break;
    }
}

/*==============================================================================
** End of file ci_custom.c
**============================================================================*/
