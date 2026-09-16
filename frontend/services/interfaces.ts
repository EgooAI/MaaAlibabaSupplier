import type { AgentConsoleState, AgentTestInput, AgentTestResult, AgentTestSession, DbAgentPreset, DocumentLlmConfig, SystemAgentDefinition } from "@/types/agent";
import type { AssistantSuggestion, Conversation, ConversationAnalysis, ConversationDetail } from "@/types/chatCanonical";
import type { ExportConversationsInput, ExportConversationsResult, RequestTranslationsInput, RequestTranslationsResult, SendMessageInput, SendMessageResult, TranslateMessageInput, TranslateMessageResult } from "@/types/chatOperations";
import type { SelfInfo } from "@/types/home";
import type { AliIdList, CreateTestTaskInput, DataDirCandidates, DataDirStatus, KeyStatus, NetworkStatus, NodeTestResult, SystemStatusSnapshot, TaskItem, TaskSnapshot } from "@/types/status";

export interface OperationsBackend {
  getSelfInfo(): Promise<SelfInfo | null>;
  resetCache(): Promise<void>;
  shutdownApp(): Promise<void>;

  requestTranslations(input: RequestTranslationsInput): Promise<RequestTranslationsResult>;
  getTranslation(text: string): Promise<string | null>;

  listConversations(): Promise<Conversation[]>;
  getConversation(id: string): Promise<ConversationDetail>;
  translateMessage(input: TranslateMessageInput): Promise<TranslateMessageResult>;
  regenerateTranslation(input: TranslateMessageInput): Promise<TranslateMessageResult>;
  getAssistantSuggestions(conversationId: string): Promise<AssistantSuggestion[]>;
  analyzeConversation(conversationId: string): Promise<ConversationAnalysis>;
  sendMessage(input: SendMessageInput): Promise<SendMessageResult>;
  exportConversations(input: ExportConversationsInput): Promise<ExportConversationsResult>;
  gotoContact(conversationId: string, loginId: string): Promise<{ status: string }>;

  checkUserStatus(): Promise<KeyStatus>;
  checkMitmProxy(): Promise<NetworkStatus>;
  checkMitmReceiver(): Promise<NetworkStatus>;
  runNodeTest(entry?: string): Promise<NodeTestResult>;
  listTaskSnapshots(): Promise<TaskSnapshot[]>;
  getSystemStatus(): Promise<SystemStatusSnapshot>;
  refreshSystemStatus(): Promise<SystemStatusSnapshot>;
  createTestTask(input: CreateTestTaskInput): Promise<TaskItem>;

  getDataDirStatus(): Promise<DataDirStatus>;
  saveDataDirPath(path: string): Promise<DataDirStatus>;
  listDataDirCandidates(): Promise<DataDirCandidates>;

  listAliIds(): Promise<AliIdList>;
  saveAliId(aliId: string): Promise<AliIdList>;
  saveAliKey(aliId: string, aesKeyHex: string): Promise<AliIdList>;
  clearAliKey(aliId: string): Promise<AliIdList>;

  getAgentConsole(): Promise<AgentConsoleState>;
  saveLlmConfig(input: DocumentLlmConfig): Promise<DocumentLlmConfig>;
  saveAgentPreset(input: DbAgentPreset): Promise<DbAgentPreset>;
  deleteAgentPreset(id: string): Promise<boolean>;
  restoreSystemAgentDefault(apid: string): Promise<DbAgentPreset>;
  listSystemAgentDefinitions(): Promise<SystemAgentDefinition[]>;
  runAgentTest(input: AgentTestInput): Promise<AgentTestResult>;
  listAgentTestHistory(): Promise<AgentTestSession[]>;
  undoAgentTestSession(id: string): Promise<AgentTestSession>;
  regenerateAgentTestSessionReply(id: string): Promise<AgentTestSession>;
  deleteAgentTestSession(id: string): Promise<void>;
  branchAgentTestSession(id: string): Promise<AgentTestSession>;
}
