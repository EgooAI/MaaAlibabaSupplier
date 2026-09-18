"use client";

import { EyeInvisibleOutlined, EyeOutlined, ReloadOutlined, TranslationOutlined } from "@ant-design/icons";
import { Button, Popconfirm, Space, Tooltip } from "antd";

export type TranslationToolbarProps = {
  visible: boolean;
  onToggleVisible: () => void;
  translatedCount: number;
  untranslatedCount: number;
  pendingCount: number;
  canTranslate: boolean;
  onTranslateMissing: () => void;
  onRetranslateAll: () => void;
};

export function TranslationToolbar({ visible, onToggleVisible, translatedCount, untranslatedCount, pendingCount, canTranslate, onTranslateMissing, onRetranslateAll }: TranslationToolbarProps) {
  const busy = pendingCount > 0;
  return (
    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-slate-100 bg-white/70 px-3 py-1.5 sm:px-4">
      <Space size={4}>
        <Tooltip title={visible ? "隐藏译文" : "显示译文"}>
          <Button
            type={visible ? "primary" : "default"}
            ghost={visible}
            size="small"
            aria-label={visible ? "隐藏译文" : "显示译文"}
            icon={visible ? <EyeOutlined /> : <EyeInvisibleOutlined />}
            aria-pressed={visible}
            onClick={onToggleVisible}
          />
        </Tooltip>
        <span className="text-xs text-slate-500 select-none" role="status">
          <TranslationOutlined className="mr-1" />
          译文 {translatedCount}/{translatedCount + untranslatedCount}
          {busy ? ` · 翻译中 ${pendingCount} 条` : ""}
        </span>
      </Space>
      <Space size={4}>
        {untranslatedCount > 0 ? (
          <Button size="small" disabled={!canTranslate || busy} onClick={onTranslateMissing}>
            翻译缺失{untranslatedCount > 0 ? ` (${untranslatedCount})` : ""}
          </Button>
        ) : null}
        <Popconfirm title="重新翻译全部买家消息？" description="会重新调用翻译模型，可能耗时较长。" okText="重新翻译" cancelText="取消" disabled={!canTranslate || busy} onConfirm={onRetranslateAll}>
          <Button size="small" icon={<ReloadOutlined />} disabled={!canTranslate || busy}>重新翻译全部</Button>
        </Popconfirm>
      </Space>
    </div>
  );
}
