import type { AgentConsoleState, AgentPresetRevision, AgentTestInput, AgentTestResult, AgentTestSession, AgentToolDefinition, BranchAgentSessionInput, DbAgentPreset, DocumentLlmConfig, ForkAgentSessionInput, SystemAgentDefinition } from "@/types/agent";
import type { AssistantSuggestionResult, ConversationAnalysis, ConversationDetail } from "@/types/chatCanonical";
import type { ConversationPage, ConversationQuery, InboxOverview, ReadReceipt } from "@/types/inbox";
import type { ComposeTranslationResult, ExportConversationsInput, ExportConversationsResult, OutboxTask, RequestTranslationsInput, RequestTranslationsResult, TranslationQueryInput, TranslationQueryResult, TranslationJobSnapshot, SendMessageInput, SendMessageResult, ConversationRevision } from "@/types/chatOperations";
import type { PortraitBatchInput, PortraitBatchResult, PortraitExportInput, PortraitExportResult, PortraitOperationEntry, PortraitTag } from "@/types/portrait";
import type { SelfInfo } from "@/types/home";
import type { AccountEpoch, ConnectionSnapshot } from "@/types/connection";
import type { InstallationNotice, UpdateState } from "@/types/update";
import type { AliIdList, CreateTestTaskInput, DataDirCandidates, DataDirStatus, KeyStatus, NetworkStatus, NodeTestEntry, NodeTestSubmission, SystemStatusSnapshot, TaskItem, TaskSnapshot } from "@/types/status";
import type { CreateExperimentInput, Experiment, ExperimentSummary, ExperimentVariantInput, RunSnapshot, UpdateExperimentInput } from "@/types/experiment";

export interface OperationsBackend {
  createRemoteSession(): Promise<{ session_id: string; expires_in: number }>;
  getRemoteFrame(sessionId: string, etag?: string): Promise<{ blob: Blob | null; etag: string | null; width: number; height: number }>;
  remoteAction(input: { session_id: string; action: "click" | "long_press" | "drag" | "type"; x?: number; y?: number; x1?: number; y1?: number; x2?: number; y2?: number; duration?: number; text?: string }): Promise<void>;
  closeRemoteSession(sessionId: string): Promise<void>;
  getConnection(): Promise<ConnectionSnapshot>;
  connectClient(epoch: AccountEpoch): Promise<ConnectionSnapshot>;
  // Submission receipt; completion is observed through committed revisions.
  retryConnection(epoch: AccountEpoch): Promise<ConnectionSnapshot>;
  getSelfInfo(): Promise<SelfInfo | null>;
  resetCache(): Promise<void>;
  shutdownApp(): Promise<{ accepted: boolean }>;
  getAppUpdate(): Promise<UpdateState>;
  checkAppUpdate(): Promise<UpdateState>;
  installAppUpdate(candidateId: string): Promise<{ accepted: true }>;
  getInstallationNotice(): Promise<InstallationNotice | null>;
  acknowledgeInstallation(id: string): Promise<InstallationNotice | null>;

  requestTranslations(input: RequestTranslationsInput): Promise<RequestTranslationsResult>;
  queryTranslations(input: TranslationQueryInput): Promise<TranslationQueryResult>;
  getTranslationJob(taskId: string): Promise<TranslationJobSnapshot | null>;

  listConversations(query?: ConversationQuery): Promise<ConversationPage>;
  markConversationRead(id: string, readSnapshot: string): Promise<ReadReceipt>;
  getInboxOverview(): Promise<InboxOverview>;
  // Pure read of committed revision and sync status, including stale archives.
  getConversationRevision(): Promise<ConversationRevision>;
  getConversation(id: string): Promise<ConversationDetail>;
  getAssistantSuggestions(conversationId: string): Promise<AssistantSuggestionResult>;
  analyzeConversation(conversationId: string): Promise<ConversationAnalysis>;
  composeTranslation(conversationId: string, text: string): Promise<ComposeTranslationResult>;
  sendMessage(input: SendMessageInput): Promise<SendMessageResult>;
  listOutbox(conversationId: string): Promise<OutboxTask[]>;
  getOutbox(id: string): Promise<OutboxTask>;
  confirmOutbox(id: string, version: number, screenshotId: string): Promise<OutboxTask>;
  cancelOutbox(id: string, version: number): Promise<OutboxTask>;
  retryOutbox(id: string, version: number): Promise<OutboxTask>;
  getOutboxScreenshot(id: string, screenshotId: string, version: number): Promise<Blob>;
  getMessageImage(conversationId: string, externalMid: string): Promise<Blob>;
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
  branchAgentTestSession(id: string, input?: number | BranchAgentSessionInput): Promise<AgentTestSession>;

  createExperiment(input: CreateExperimentInput): Promise<Experiment>;
  listExperiments(agentId?: string): Promise<ExperimentSummary[]>;
  getExperiment(id: string): Promise<Experiment>;
  updateExperiment(id: string, input: UpdateExperimentInput): Promise<Experiment>;
  addExperimentStep(id: string, input: { mode: "buyer" | "agent"; text: string; label?: string }): Promise<Experiment>;
  addExperimentVariant(id: string, input: ExperimentVariantInput): Promise<Experiment>;
  removeExperimentVariant(id: string, variantId: string): Promise<Experiment>;
  deleteExperiment(id: string): Promise<void>;
  // Submission receipt; the caller polls the run until it reaches a terminal state.
  startExperimentRun(id: string, stepIndex: number, force?: boolean): Promise<RunSnapshot>;
  getExperimentRun(runId: string): Promise<RunSnapshot>;
  cancelExperimentRun(runId: string): Promise<RunSnapshot>;
  promoteExperimentVariant(id: string, variantId: string, note?: string): Promise<DbAgentPreset>;
}
