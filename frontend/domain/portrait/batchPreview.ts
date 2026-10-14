import { lostReasonLabel, portraitStageLabel } from "./portraitModel";
import type { BusinessTag, PortraitChangesInput } from "@/types/portrait";
import type { Conversation } from "@/types/chatCanonical";

export type BatchPanelAction = "stage" | "add-tag" | "remove-tag";

/** 配置面板当前视图：三个画像动作 + 两个导出预览（导出同样先预览后确认）。 */
export type BatchPanelView = BatchPanelAction | "export-portrait" | "export-chat";

export type PortraitPreviewStatus = "change" | "unchanged" | "unavailable";

export interface PortraitPreviewRow {
  conversation: Conversation;
  status: PortraitPreviewStatus;
  /** unchanged / unavailable 的原因。 */
  note: string;
  /** change 行的 before → after 描述。 */
  lines: string[];
}

const MAX_BATCH_SESSIONS = 50;

function tagName(tagId: string, targets: Conversation[], catalog: BusinessTag[]): string {
  for (const tag of catalog) if (tag.tag_id === tagId) return tag.archived ? `${tag.name}（已停用）` : tag.name;
  for (const conversation of targets) {
    for (const tag of conversation.customer.businessTags) if (tag.tag_id === tagId) return tag.name;
  }
  return tagId;
}

/**
 * 客户端预演：镜像服务端语义（流失信息仅在生效阶段为流失时有效、已持有标签=无变化、
 * 已停用标签可移除不可新增），仅用于提交前展示，服务端仍是唯一权威。
 */
export function previewPortraitChanges(changes: PortraitChangesInput, targets: Conversation[], catalog: BusinessTag[] = []): PortraitPreviewRow[] {
  const addIds = changes.add_tag_ids ?? [];
  const removeIds = changes.remove_tag_ids ?? [];
  return targets.map((conversation) => {
    const customer = conversation.customer;
    if (customer.cid === null) {
      return { conversation, status: "unavailable" as const, note: "无法关联平台客户，跳过画像", lines: [] };
    }
    const held = new Set(customer.businessTags.map((tag) => tag.tag_id));
    const add = addIds.filter((id) => !held.has(id));
    const remove = removeIds.filter((id) => held.has(id));
    const lines: string[] = [];
    let unchangedNote = "";

    if (changes.stage !== undefined) {
      if (changes.stage === customer.businessStage) {
        if (changes.stage === "lost") {
          const nextReason = changes.lost_reason ?? customer.lostReason;
          const nextNote = (changes.lost_note ?? customer.lostNote ?? "").trim();
          const sameReason = nextReason === customer.lostReason;
          const sameNote = nextNote === (customer.lostNote ?? "").trim();
          if (sameReason && sameNote) unchangedNote = "已是「流失/失败」，失败信息一致";
          else lines.push(`更新失败信息：原因「${lostReasonLabel(nextReason)}」${nextNote ? "＋备注" : ""}`);
        } else {
          unchangedNote = `已是「${portraitStageLabel(changes.stage)}」`;
        }
      } else {
        lines.push(`阶段：${portraitStageLabel(customer.businessStage)} → ${portraitStageLabel(changes.stage)}`);
        if (customer.businessStage === "lost") lines.push("清除失败信息");
        if (changes.stage === "lost") {
          lines.push(`标记流失：原因「${lostReasonLabel(changes.lost_reason ?? "unclear")}」${changes.lost_note?.trim() ? "＋备注" : ""}`);
        }
      }
    } else if (changes.lost_reason !== undefined || changes.lost_note !== undefined) {
      if (customer.businessStage !== "lost") {
        return { conversation, status: "unavailable" as const, note: "失败信息仅在流失阶段维护", lines: [] };
      }
      const nextReason = changes.lost_reason ?? customer.lostReason;
      const nextNote = (changes.lost_note ?? customer.lostNote ?? "").trim();
      const sameReason = nextReason === customer.lostReason;
      const sameNote = nextNote === (customer.lostNote ?? "").trim();
      if (sameReason && sameNote) unchangedNote = "失败信息一致";
      else lines.push(`更新失败信息：原因「${lostReasonLabel(nextReason)}」${nextNote ? "＋备注" : ""}`);
    }

    if (add.length) lines.push(`＋${add.map((id) => tagName(id, targets, catalog)).join("、")}`);
    if (remove.length) lines.push(`－${remove.map((id) => tagName(id, targets, catalog)).join("、")}`);

    if (lines.length) return { conversation, status: "change" as const, note: "", lines };
    const note = unchangedNote || (addIds.length ? "已持有所选标签" : removeIds.length ? "未持有所选标签" : "没有变化");
    return { conversation, status: "unchanged" as const, note, lines: [] };
  });
}

/** 提交前校验；返回错误文案或 null。 */
export function portraitChangesError(changes: PortraitChangesInput, targets: Conversation[], catalog: BusinessTag[] = []): string | null {
  const add = changes.add_tag_ids ?? [];
  const remove = changes.remove_tag_ids ?? [];
  const hasAny = changes.stage !== undefined || changes.lost_reason !== undefined || changes.lost_note !== undefined || add.length > 0 || remove.length > 0;
  if (!hasAny) return "请先设置要变更的内容";
  const resolvable = targets.filter((conversation) => conversation.customer.cid !== null);
  if (!resolvable.length) return "所选会话缺少可维护的客户身份";
  if (resolvable.length > MAX_BATCH_SESSIONS) return `单批最多 ${MAX_BATCH_SESSIONS} 个会话`;
  if (changes.stage === "lost" && changes.lost_reason === undefined) return "标记流失需选择失败原因";
  if (remove.some((id) => add.includes(id))) return "同一标签不能同时增加和移除";
  const archived = add.filter((id) => catalog.some((tag) => tag.tag_id === id && tag.archived));
  if (archived.length) return `标签「${archived.map((id) => tagName(id, targets, catalog)).join("、")}」已停用，不能新增`;
  if (changes.lost_reason !== undefined || changes.lost_note !== undefined) {
    const invalid = resolvable.some((conversation) => (changes.stage ?? conversation.customer.businessStage) !== "lost");
    if (invalid) return "失败原因和备注仅在流失阶段维护";
  }
  return null;
}
