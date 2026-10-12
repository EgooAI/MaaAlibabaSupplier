export type PortraitStage =
  | "unknown"
  | "new_inquiry"
  | "following_up"
  | "negotiating"
  | "won"
  | "dormant"
  | "lost";

export type LostReason = "unreachable" | "price" | "product_mismatch" | "delivery" | "competitor" | "unclear";

export interface BusinessTag {
  tag_id: string;
  name: string;
  archived?: boolean;
}

export interface PortraitTag extends BusinessTag {
  description: string;
  archived: boolean;
  version: number;
  created_at: number;
  updated_at: number;
  usage_count: number;
}

export interface PortraitTargetInput {
  sid: number;
  expected_cid: number;
  expected_version: number;
}

export interface PortraitChangesInput {
  stage?: PortraitStage;
  lost_reason?: LostReason;
  lost_note?: string;
  add_tag_ids?: string[];
  remove_tag_ids?: string[];
}

export interface PortraitBatchInput {
  request_id: string;
  targets: PortraitTargetInput[];
  changes: PortraitChangesInput;
}

export interface PortraitBatchTargetResult {
  cid: number;
  sids: number[];
  version: number;
}

export interface PortraitBatchResult {
  request_id: string;
  portrait_revision: number;
  tag_revision: number;
  changed: PortraitBatchTargetResult[];
  unchanged: PortraitBatchTargetResult[];
}

export interface PortraitExportInput {
  conversationIds: number[];
}

export interface PortraitExportResult {
  filename: string;
  content: string;
  exported: number;
  missing: number[];
}

export interface PortraitOperationEntry {
  request_id: string;
  kind: string;
  created_at: number;
  changes: PortraitChangesInput;
  targets: PortraitBatchTargetResult[];
}
