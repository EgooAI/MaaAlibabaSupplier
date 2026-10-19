import type { AgentConsoleState, AgentPresetRevision, AgentTestInput, AgentTestResult, AgentTestSession, AgentToolDefinition, DbAgentPreset, DocumentLlmConfig, ForkAgentSessionInput, SystemAgentDefinition } from "@/types/agent";
import type { AssistantSuggestionResult, ConversationAnalysis, ConversationDetail } from "@/types/chatCanonical";
import type { ConversationPage, ConversationQuery, InboxOverview, InboxSettings, ReadReceipt } from "@/types/inbox";
import type { ExportConversationsInput, ExportConversationsResult, OutboxTask, RequestTranslationsInput, RequestTranslationsResult, TranslationQueryInput, TranslationQueryResult, TranslationJobSnapshot, SendMessageInput, SendMessageResult, ConversationRevision } from "@/types/chatOperations";
import type { PortraitBatchInput, PortraitBatchResult, PortraitExportInput, PortraitExportResult, PortraitOperationEntry, PortraitTag } from "@/types/portrait";
import type { SelfInfo } from "@/types/home";
import type { AccountEpoch, ConnectionSnapshot } from "@/types/connection";
import type { UpdateState } from "@/types/update";
import type { AliIdList, CreateTestTaskInput, DataDirCandidates, DataDirStatus, KeyStatus, NetworkStatus, NodeTestEntry, NodeTestSubmission, SystemStatusSnapshot, TaskItem, TaskSnapshot } from "@/types/status";

export interface OperationsBackend {
  getConnection(): Promise<ConnectionSnapshot>;
  connectClient(epoch: AccountEpoch): Promise<ConnectionSnapshot>;
  // Submission receipt; completion is observed through committed revisions.
  retryConnection(epoch: AccountEpoch): Promise<ConnectionSnapshot>;
  getSelfInfo(): Promise<SelfInfo | null>;
  resetCache(): Promise<void>;
  shutdownApp(): Promise<{ accepted: boolean }>;
  getAppUpdate(): Promise<UpdateState>;
  checkAppUpdate(): Promise<UpdateState>;
  downloadAppUpdate(candidateId: string): Promise<UpdateState>;
  installAppUpdate(candidateId: string): Promise<{ accepted: true }>;

  requestTranslations(input: RequestTranslationsInput): Promise<RequestTranslationsResult>;
  queryTranslations(input: TranslationQueryInput): Promise<TranslationQueryResult>;
  getTranslationJob(taskId: string): Promise<TranslationJobSnapshot | null>;

  listConversations(query?: ConversationQuery): Promise<ConversationPage>;
  markConversationRead(id: string, readSnapshot: string): Promise<ReadReceipt>;
  getInboxOverview(): Promise<InboxOverview>;
  getInboxSettings(): Promise<InboxSettings>;
  saveInboxSettings(timeoutSeconds: number): Promise<InboxSettings>;
  // Pure read of committed revision and sync status, including stale archives.
  getConversationRevision(): Promise<ConversationRevision>;
  getConversation(id: string): Promise<ConversationDetail>;
  getAssistantSuggestions(conversationId: string): Promise<AssistantSuggestionResult>;
  analyzeConversation(conversationId: string): Promise<ConversationAnalysis>;
  sendMessage(input: SendMessageInput): Promise<SendMessageResult>;
  listOutbox(conversationId: string): Promise<OutboxTask[]>;
  getOutbox(id: string): Promise<OutboxTask>;
  confirmOutbox(id: string, version: number, screenshotId: string): Promise<OutboxTask>;
  cancelOutbox(id: string, version: number): Promise<OutboxTask>;
  retryOutbox(id: string, version: number): Promise<OutboxTask>;
  getOutboxScreenshot(id: string, screenshotId: string, version: number): Promise<Blob>;
  exportConversations(input: ExportConversationsInput): Promise<ExportConversationsResult>;
  gotoContact(conversationId: string, loginId: string): Promise<{ status: string }>;

  listPortraitTags(): Promise<{ tags: PortraitTag[] }>;
  createPortraitTag(input: { name: string; description?: string }): Promise<{ tag: PortraitTag }>;
  updatePortraitTag(tagId: string, input: { name?: string; description?: string; archived?: boolean }): Promise<{ tag: PortraitTag }>;
  savePortraitBatch(input: PortraitBatchInput): Promise<PortraitBatchResult>;
  exportPortraits(input: PortraitExportInput): Promise<PortraitExportResult>;
  listPortraitOperations(cid: number): Promise<{ operations: PortraitOperationEntry[] }>;

  checkUserStatus(): Promise<KeyStatus>;
  checkMitmProxy(): Promise<NetworkStatus>;
  checkMitmReceiver(): Promise<NetworkStatus>;
  runNodeTest(entry?: NodeTestEntry): Promise<NodeTestSubmission>;
  sweepCards(): Promise<void>;
  listTaskSnapshots(): Promise<TaskSnapshot[]>;
  getSystemStatus(): Promise<SystemStatusSnapshot>;
  createTestTask(input: CreateTestTaskInput): Promise<TaskItem>;

  getDataDirStatus(): Promise<DataDirStatus>;
  saveDataDirPath(path: string, epoch: AccountEpoch): Promise<DataDirStatus>;
  listDataDirCandidates(): Promise<DataDirCandidates>;

  listAliIds(): Promise<AliIdList>;
  saveAliId(aliId: string, epoch: AccountEpoch): Promise<AliIdList>;
  saveAliKey(aliId: string, aesKeyHex: string, epoch: AccountEpoch): Promise<AliIdList>;
  clearAliKey(aliId: string, epoch: AccountEpoch): Promise<AliIdList>;

  getAgentConsole(): Promise<AgentConsoleState>;
  listAgentTools(): Promise<AgentToolDefinition[]>;
  saveLlmConfig(input: DocumentLlmConfig): Promise<DocumentLlmConfig>;
  saveAgentPreset(input: DbAgentPreset): Promise<DbAgentPreset>;
  deleteAgentPreset(id: string): Promise<boolean>;
  restoreSystemAgentDefault(apid: string): Promise<DbAgentPreset>;
  listSystemAgentDefinitions(): Promise<SystemAgentDefinition[]>;
  listAgentPresetRevisions(apid: string, limit?: number): Promise<AgentPresetRevision[]>;
  runAgentTest(input: AgentTestInput): Promise<AgentTestResult>;
  listAgentTestHistory(): Promise<AgentTestSession[]>;
  getAgentSession(id: string): Promise<{ session: AgentTestSession; frozenInput: string }>;
  forkAgentSession(input: ForkAgentSessionInput): Promise<AgentTestSession>;
  undoAgentTestSession(id: string): Promise<AgentTestSession>;
  regenerateAgentTestSessionReply(id: string): Promise<AgentTestSession>;
  deleteAgentTestSession(id: string): Promise<void>;
  branchAgentTestSession(id: string, messageIndex?: number): Promise<AgentTestSession>;
}
