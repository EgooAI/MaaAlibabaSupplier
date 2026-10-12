import type { BusinessTag, LostReason, PortraitOperationEntry, PortraitStage } from "@/types/portrait";

const stageLabels: Record<PortraitStage, string> = {
  unknown: "未设置",
  new_inquiry: "新咨询",
  following_up: "跟进中",
  negotiating: "报价/洽谈",
  won: "已成交",
  dormant: "沉默/待唤醒",
  lost: "流失/失败",
};

const lostReasonLabels: Record<LostReason, string> = {
  unreachable: "失联",
  price: "价格",
  product_mismatch: "产品不匹配",
  delivery: "交期/物流",
  competitor: "转向竞品",
  unclear: "未明确",
};

export const PORTRAIT_STAGE_OPTIONS: { value: PortraitStage; label: string }[] =
  (Object.keys(stageLabels) as PortraitStage[]).map((value) => ({ value, label: stageLabels[value] }));

export const LOST_REASON_OPTIONS: { value: LostReason; label: string }[] =
  (Object.keys(lostReasonLabels) as LostReason[]).map((value) => ({ value, label: lostReasonLabels[value] }));

export function portraitStageLabel(stage: PortraitStage | undefined) {
  return stage ? stageLabels[stage] ?? stage : stageLabels.unknown;
}

export function portraitStageColor(stage: PortraitStage | undefined) {
  return ({
    unknown: "default",
    new_inquiry: "blue",
    following_up: "geekblue",
    negotiating: "gold",
    won: "green",
    dormant: "orange",
    lost: "red",
  } as Record<string, string>)[stage ?? "unknown"] ?? "default";
}

export function lostReasonLabel(reason: LostReason | null | undefined) {
  return reason ? lostReasonLabels[reason] ?? reason : "";
}

export function portraitTagLabel(tag: BusinessTag) {
  return tag.archived ? `${tag.name}（已停用）` : tag.name;
}

export function portraitTagOptions(tags: BusinessTag[]) {
  return tags.map((tag) => ({ value: tag.tag_id, label: portraitTagLabel(tag) }));
}

export function portraitRequestId() {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  return random.slice(0, 64);
}

/** 将一次画像操作记录格式化为“阶段→…；加标签：…”形式的摘要。 */
export function operationSummary(entry: PortraitOperationEntry, tagOptions: { value: string; label: string }[]) {
  const names = new Map(tagOptions.map((option) => [option.value, option.label]));
  const parts: string[] = [];
  if (entry.changes.stage) parts.push(`阶段→${portraitStageLabel(entry.changes.stage)}`);
  if (entry.changes.lost_reason) parts.push(`原因→${lostReasonLabel(entry.changes.lost_reason)}`);
  if (entry.changes.add_tag_ids?.length) {
    parts.push(`加标签：${entry.changes.add_tag_ids.map((tagId) => names.get(tagId) ?? tagId).join("、")}`);
  }
  if (entry.changes.remove_tag_ids?.length) {
    parts.push(`移除标签：${entry.changes.remove_tag_ids.map((tagId) => names.get(tagId) ?? tagId).join("、")}`);
  }
  return parts.join("；") || "画像变更";
}
