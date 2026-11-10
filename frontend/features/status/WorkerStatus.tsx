"use client";

import { Alert, Card, Col, Collapse, Row, Space, Tag, Typography } from "antd";
import { AppTable } from "@/components/AppTable";
import {
  observationTime, runtimeRows, runtimeStatus, serviceSummaries,
  type ServiceSummary, type WorkStatus,
} from "@/domain/status/backgroundModel";
import type { SystemStatusSnapshot } from "@/types/status";

export function WorkStateTag({ status }: { status: WorkStatus }) {
  return <Tag color={status.color}>{status.label}</Tag>;
}

function ServiceCard({ service }: { service: ServiceSummary }) {
  return (
    <Card size="small" className="h-full" title={service.title} extra={<WorkStateTag status={service.status} />}>
      <Space orientation="vertical" size="small" className="w-full">
        {service.details.map((detail) => <Typography.Text key={detail}>{detail}</Typography.Text>)}
        {service.resources.map((resource, index) => (
          <div key={`${resource.name}:${resource.started_at}:${index}`}>
            <Typography.Text type="secondary">{resource.name} </Typography.Text>
            <WorkStateTag status={runtimeStatus([resource])} />
            {resource.last_error ? <Typography.Text type="warning">上次异常：{resource.last_error}</Typography.Text> : null}
          </div>
        ))}
        {service.error ? <Typography.Text type="danger">{service.error}</Typography.Text> : null}
      </Space>
    </Card>
  );
}

const workerNames: Record<string, string> = {
  "im-source-check": "源库检查服务", "outbox-verifier": "发送结果核对服务", "card-sweep": "卡片清扫服务",
};
const phases: Record<string, string> = {
  not_started: "未启动", starting: "启动中", checking_source: "检查源库", waiting: "等待下一轮",
  reconciling: "核对中", scanning: "读取待核对任务", reading_source: "读取本地消息", stopped: "循环已退出",
  selecting: "筛选目标", sweeping: "导航会话", idle: "等待手动触发",
};
const poolNames: Record<string, string> = { product_cards: "产品卡池", inquiry_cards: "询盘卡池", user_info: "联系人池" };

function WorkerObservations({ snapshot }: { snapshot: SystemStatusSnapshot }) {
  return (
    <Space orientation="vertical" size="middle" className="w-full">
      {Object.entries(snapshot.workers ?? {}).map(([name, worker]) => (
        <div key={name}>
          <Typography.Text strong>{workerNames[name] ?? name} </Typography.Text>
          {worker ? (
            <>
              <Tag>{!worker.started ? "未启动" : !worker.alive ? "线程已停止" : worker.stopping ? "停止中" : "线程运行中"}</Tag>
              <Typography.Paragraph className="mb-1">
                阶段：{phases[worker.phase] ?? worker.phase} · 持续 {worker.phase_age_s == null ? "未知" : `${Math.floor(worker.phase_age_s)} 秒`} · 已完成循环 {worker.completed_iterations}
                {name !== "card-sweep" ? ` · ${name === "im-source-check" ? "待提交同步目标" : "待核对数量"}：${worker.pending ?? "未观察 / 不适用"}` : ""}
              </Typography.Paragraph>
              <Typography.Text type="secondary">启动：{observationTime(worker.started_at)} · 最近心跳：{observationTime(worker.heartbeat_at)} · 最近进展：{observationTime(worker.last_progress_at)}</Typography.Text>
              {worker.last_error ? <Typography.Paragraph type="warning">上次循环异常：{worker.last_error}</Typography.Paragraph> : null}
            </>
          ) : <Typography.Text type="secondary">尚无观察记录</Typography.Text>}
        </div>
      ))}
      {Object.entries(snapshot.queues ?? {}).map(([name, queue]) => (
        <div key={name}>
          <Typography.Text strong>{name === "maafw" ? "GUI" : name === "translation" ? "翻译" : name} 执行队列 </Typography.Text>
          <Tag>{!queue.initialized ? "未启动" : queue.alive ? "线程运行中" : "线程已停止"}</Tag>
          <Typography.Text>进程内排队 {queue.pending} · 当前任务：{queue.current_age_s == null ? "无" : `已运行 ${Math.floor(queue.current_age_s)} 秒`} · 最近完成：{observationTime(queue.last_completed)}</Typography.Text>
        </div>
      ))}
      {!snapshot.workers && !snapshot.queues ? <Typography.Text type="secondary">未提供工作循环观察</Typography.Text> : null}
    </Space>
  );
}

export function WorkerStatus({ snapshot }: { snapshot: SystemStatusSnapshot }) {
  const background = snapshot.background;
  const summaries = serviceSummaries(snapshot);
  return (
    <Space orientation="vertical" size="large" className="w-full">
      {!background ? <Alert type="info" showIcon title="当前快照未提供统一后台数据" description="仍可查看已有同步、诊断与工作循环观察。业务队列、实验、Outbox 和运行时状态尚未提供。" /> : null}
      {background?.runtime.closing ? <Alert type="warning" showIcon title="后台正在停止，等待已接收工作结束" /> : null}
      {background && Object.keys(background.errors).length ? <Alert type="warning" showIcon title="部分快照读取失败" description={Object.entries(background.errors).map(([name, error]) => <div key={name}>{name}：{error}</div>)} /> : null}
      <Typography.Text type="secondary">业务列表仅含当前工作区。执行器和缓存池为进程范围观察。</Typography.Text>
      {(["数据处理", "业务任务", "运行维护"] as const).map((category) => (
        <section key={category} aria-label={category}>
          <Typography.Title level={5} className="mt-0">{category}</Typography.Title>
          <Row gutter={[16, 16]}>
            {summaries.filter((service) => service.category === category).map((service) => <Col xs={24} md={12} xl={8} key={service.id}><ServiceCard service={service} /></Col>)}
          </Row>
        </section>
      ))}
      <Collapse items={[
        {
          key: "runtime", label: "基础执行器详情与已结束历史",
          children: !background ? <Typography.Text type="secondary">未提供运行时快照</Typography.Text> : (
            <Space orientation="vertical" className="w-full">
              <Typography.Text type="secondary">观察于 {observationTime(background.runtime.observed_at)}。计数为执行器生命周期累计。</Typography.Text>
              <AppTable
                rowKey="key" size="small" dataSource={runtimeRows(background.runtime.resources)} scroll={{ x: 1200 }}
                columns={[
                  { title: "执行器 / 类型", width: 240, render: (_, resource) => <><div>{resource.name}</div><Typography.Text type="secondary">{resource.kind} · {resource.instanceLabel}</Typography.Text></> },
                  { title: "状态", width: 130, render: (_, resource) => <WorkStateTag status={runtimeStatus([resource])} /> },
                  { title: "执行 / 排队", width: 120, render: (_, resource) => `${resource.active} / ${resource.pending}` },
                  { title: "累计完成 / 异常", width: 140, render: (_, resource) => `${resource.completed} / ${resource.failed}` },
                  { title: "实例启动", width: 170, render: (_, resource) => observationTime(resource.started_at) },
                  { title: "最近执行 / 完成 / 下次计划", width: 220, render: (_, resource) => <>{observationTime(resource.last_started_at)}<br />{observationTime(resource.last_completed_at)}<br />{observationTime(resource.next_run_at)}</> },
                  { title: "上次异常", dataIndex: "last_error", render: (value: string | null) => value || "无记录" },
                ]}
                locale={{ emptyText: "尚无执行器记录" }}
              />
            </Space>
          ),
        },
        { key: "workers", label: "工作循环与队列观察", children: <WorkerObservations snapshot={snapshot} /> },
        {
          key: "pools", label: "缓存池计数",
          children: snapshot.pools ? <Space wrap>{Object.entries(snapshot.pools).map(([name, count]) => <Tag key={name}>{poolNames[name] ?? name}：{count}</Tag>)}{!Object.keys(snapshot.pools).length ? <Typography.Text type="secondary">暂无缓存池记录</Typography.Text> : null}</Space> : <Typography.Text type="secondary">缓存池数据未提供</Typography.Text>,
        },
      ]} />
    </Space>
  );
}
