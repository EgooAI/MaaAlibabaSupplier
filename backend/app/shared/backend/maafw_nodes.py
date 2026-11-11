"""Pipeline and diagnostic node names shared by logging, status and the runner."""

PIPELINE_NODE_NAMES = frozenset({
    "ChatInput",
    "ChatInput_GoToInput",
    "ChatInput_SelectAll",
    "ChatInput_SelectAll_A",
    "ChatInput_SelectAll_ReleaseCtrl",
    "ChatInput_ClearText",
    "ChatInput_InputText",
    "ChatInput_SendOnly",
    "ChatInput_SendMessage",
    "Common_DismissLowQualityDialog",
    "ContactSearch",
    "ContactSearch_GoToSearch",
    "ContactSearch_FindSearchBox",
    "ContactSearch_ClickSearchButton",
    "ContactSearch_ClearSearchBox",
    "ContactSearch_InputText",
    "ContactSearch_PressEnter",
    "Diagnostics_ChatInput",
    "Diagnostics_ChatInput_Recognize",
    "Diagnostics_ContactSearch",
    "Diagnostics_ContactSearch_Recognize",
})

DIAGNOSTIC_NODE_NAMES = frozenset({
    "Diagnostics_ChatInput",
    "Diagnostics_ContactSearch",
})

MANUAL_NODE_TESTS = {
    "ChatInput_GoToInput": "Diagnostics_ChatInput",
    "ContactSearch_GoToSearch": "Diagnostics_ContactSearch",
}
