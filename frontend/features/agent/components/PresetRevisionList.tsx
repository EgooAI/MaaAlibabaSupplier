"use client";

import { ReloadOutlined } from "@ant-design/icons";
import { Alert, App, Button, Empty, List, Modal, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { backend } from "@/services/client";
import type { AgentPresetRevision } from "@/types/agent";
import { PromptDiffView } from "./PromptDiffView";

const SOURCE_LABELS: Record<string, string> = {
  manual: "手动保存",
  restore_default: "恢复默认",
  seed: "内置默认",
};

type PresetRevisionListProps = {
  apid?: string;
  currentPrompt: string;
  onLoad: (revision: AgentPresetRevision) => void;
};

/** Append-only preset history; loading an entry writes it into the editor draft. */
export function PresetRevisionList({ apid, currentPrompt, onLoad }: PresetRevisionListProps) {
  const { message } = App.useApp();
  const [revisions, setRevisions] = useState<AgentPresetRevision[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>();
  const [comparing, setComparing] = useState<AgentPresetRevision>();
  const [refreshNonce, setRefreshNonce] = useState(0);

  useEffect(() => {
    if (!apid) return;
    let active = true;
    backend.listAgentPresetRevisions(apid)
      .then((items) => {
        if (!active) return;
        setRevisions(items);
        setError(undefined);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof Error ? caught.message : "历史版本加载失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apid, refreshNonce]);

  function refresh() {
    setLoading(true);
    setError(undefined);
    setRefreshNonce((current) => current + 1);
  }

  if (!apid) {
    return <Alert type="info" showIcon message="Agent 尚未保存，保存后这里会出现版本历史。" />;
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <Typography.Text type="secondary" className="text-xs">
          每次保存或恢复默认后追加一条快照，最多保留 50 条。
        </Typography.Text>
        <Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={refresh}>刷新</Button>
      </div>
      {error ? <Alert type="warning" showIcon message={error} /> : null}
      <List
        size="small"
        loading={loading}
        dataSource={revisions}
        locale={{ emptyText: <Empty description="暂无历史版本" /> }}
        renderItem={(revision) => (
          <List.Item
            actions={[
              <Button key="compare" type="link" size="small" onClick={() => setComparing(revision)}>对比当前</Button>,
              <Button key="load" type="link" size="small" onClick={() => { onLoad(revision); message.success("已载入到编辑器，保存后生效"); }}>载入</Button>,
            ]}
          >
            <List.Item.Meta
              title={(
                <Space size="small" wrap>
                  <Typography.Text strong>{revision.created_at || "未知时间"}</Typography.Text>
                  <Tag color={revision.source === "restore_default" ? "gold" : "default"}>
                    {SOURCE_LABELS[revision.source] ?? revision.source}
                  </Tag>
                </Space>
              )}
              description={(
                <Typography.Text type="secondary" className="text-xs" ellipsis>
                  {revision.note || revision.prompt.slice(0, 60) || "（空提示词）"}
                </Typography.Text>
              )}
            />
          </List.Item>
        )}
      />

      <Modal
        open={Boolean(comparing)}
        title="与当前草稿对比"
        footer={null}
        width={960}
        destroyOnHidden
        onCancel={() => setComparing(undefined)}
      >
        <Typography.Paragraph type="secondary" className="text-xs">
          左侧为当前编辑器草稿，右侧为该历史版本（点击“载入”可写回编辑器）。
        </Typography.Paragraph>
        <PromptDiffView
          before={currentPrompt}
          after={comparing?.prompt ?? ""}
          maxHeight={520}
        />
      </Modal>
    </div>
  );
}
