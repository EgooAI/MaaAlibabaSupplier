import { describe, expect, it } from "vitest";
import { portraitChangesError, previewPortraitChanges } from "@/domain/portrait/batchPreview";
import type { Conversation } from "@/types/chatCanonical";
import type { BusinessTag, LostReason, PortraitStage } from "@/types/portrait";

function conversation(id: string, cid: number | null, options: { stage?: PortraitStage; tags?: BusinessTag[]; lostReason?: LostReason | null; lostNote?: string | null } = {}): Conversation {
  return {
    id,
    customer: {
      id: cid === null ? `c-${id}` : String(cid),
      name: `buyer-${id}`,
      company: "Gulf Trade",
      country: "阿联酋",
      email: "",
      phone: "",
      stage: "unknown",
      tags: [],
      cid,
      businessStage: options.stage ?? "unknown",
      businessTags: options.tags ?? [],
      businessVersion: options.tags || options.stage ? 1 : 0,
      lostReason: options.lostReason ?? null,
      lostNote: options.lostNote ?? null,
      lostAt: null,
      portraitEditable: cid !== null,
      availability: "",
      behavior: [],
    },
    latestMessage: "hello",
    updatedAt: "2026-09-17 10:00",
    unreadCount: 0,
    replyState: "none",
    pendingSince: null,
    dueAt: null,
    isOverdue: false,
    historyPending: false,
    uncertain: false,
  };
}

const tag = (tag_id: string, name: string, archived = false): BusinessTag => ({ tag_id, name, archived });

describe("previewPortraitChanges", () => {
  it("describes stage transitions, lost entry and lost clearing", () => {
    const following = conversation("42", 1001, { stage: "following_up" });
    expect(previewPortraitChanges({ stage: "negotiating" }, [following])[0]).toMatchObject({
      status: "change",
      lines: ["阶段：跟进中 → 报价/洽谈"],
    });
    expect(previewPortraitChanges({ stage: "lost", lost_reason: "price", lost_note: "贵" }, [following])[0].lines).toEqual([
      "阶段：跟进中 → 流失/失败",
      "标记流失：原因「价格」＋备注",
    ]);
    const lost = conversation("43", 1002, { stage: "lost", lostReason: "price", lostNote: "贵" });
    expect(previewPortraitChanges({ stage: "won" }, [lost])[0].lines).toEqual(["阶段：流失/失败 → 已成交", "清除失败信息"]);
  });

  it("reports no-op stage and lost edits as unchanged", () => {
    const following = conversation("42", 1001, { stage: "following_up" });
    expect(previewPortraitChanges({ stage: "following_up" }, [following])[0]).toMatchObject({ status: "unchanged", note: "已是「跟进中」" });
    const lost = conversation("43", 1002, { stage: "lost", lostReason: "price", lostNote: "贵" });
    expect(previewPortraitChanges({ stage: "lost", lost_reason: "price", lost_note: "贵" }, [lost])[0]).toMatchObject({ status: "unchanged", note: "已是「流失/失败」，失败信息一致" });
    // 仅改备注（不重述阶段）在已流失客户上仍是有效变更。
    expect(previewPortraitChanges({ lost_note: "同意账期后再谈" }, [lost])[0]).toMatchObject({ status: "change", lines: ["更新失败信息：原因「价格」＋备注"] });
    expect(previewPortraitChanges({ lost_reason: "price" }, [following])[0]).toMatchObject({ status: "unavailable", note: "失败信息仅在流失阶段维护" });
  });

  it("computes tag diffs and marks unresolved sessions", () => {
    const held = conversation("42", 1001, { tags: [tag("t1", "长期合作")] });
    expect(previewPortraitChanges({ add_tag_ids: ["t1", "t2"] }, [held], [tag("t2", "大单")])[0]).toMatchObject({ status: "change", lines: ["＋大单"] });
    expect(previewPortraitChanges({ add_tag_ids: ["t1"] }, [held])[0]).toMatchObject({ status: "unchanged", note: "已持有所选标签" });
    expect(previewPortraitChanges({ remove_tag_ids: ["t2"] }, [held], [tag("t2", "大单")])[0]).toMatchObject({ status: "unchanged", note: "未持有所选标签" });
    expect(previewPortraitChanges({ remove_tag_ids: ["t1"] }, [held])[0]).toMatchObject({ status: "change", lines: ["－长期合作"] });
    expect(previewPortraitChanges({ add_tag_ids: ["t1"] }, [conversation("45", null)])[0]).toMatchObject({ status: "unavailable", note: "无法关联平台客户，跳过画像" });
  });
});

describe("portraitChangesError", () => {
  const targets = [conversation("42", 1001, { stage: "following_up" }), conversation("45", null)];

  it("rejects empty, oversized and conflicting change sets", () => {
    expect(portraitChangesError({}, targets)).toBe("请先设置要变更的内容");
    expect(portraitChangesError({ add_tag_ids: ["t1"], remove_tag_ids: ["t1"] }, targets)).toBe("同一标签不能同时增加和移除");
    expect(portraitChangesError({ stage: "lost" }, targets)).toBe("标记流失需选择失败原因");
    expect(portraitChangesError({ stage: "won" }, Array.from({ length: 51 }, (_, index) => conversation(String(index + 1), 1000 + index)))).toBe("单批最多 50 个会话");
    expect(portraitChangesError({ stage: "won" }, [conversation("45", null)])).toBe("所选会话缺少可维护的客户身份");
    expect(portraitChangesError({ add_tag_ids: ["t5"] }, targets, [tag("t5", "展会名片", true)])).toBe("标签「展会名片（已停用）」已停用，不能新增");
  });

  it("accepts valid change sets", () => {
    expect(portraitChangesError({ stage: "won" }, targets)).toBeNull();
    expect(portraitChangesError({ stage: "lost", lost_reason: "price" }, [conversation("42", 1001)])).toBeNull();
    expect(portraitChangesError({ lost_note: "备注" }, [conversation("42", 1001, { stage: "lost", lostReason: "price" })])).toBeNull();
  });
});
