import { z } from "zod";

export const key = z.string().regex(/^[A-Za-z0-9_-]{1,96}$/);
const count = z.number().int().nonnegative();
const kind = z.enum(["episode", "repair_experience", "project_constraint"]);
export const draftSchema = z.object({ kind, connection_id: key, event_id: key, tool_id: key, source_version: key, title: z.string().min(1).max(160), body: z.string().min(1).max(16384) });
export const memorySchema = z.object({ receipt: z.object({ resource_id: key, version: count, revision: count, state: z.enum(["draft", "confirmed", "revoked", "deleted"]) }), kind, digest: z.string().max(64), content: draftSchema.nullable() });
const connectionSchema = z.object({ connection_id: key, project_id: key, origin: z.enum(["external_report", "local_user", "synthetic"]), enabled: z.boolean(), version: count, credential_version: count, credential_active: z.boolean() });
export const grantSchema = z.object({ connection_id: key, content_type: z.enum(["query", "episode", "repair_experience", "project_constraint"]), purpose: z.enum(["index", "retrieve", "agent_context"]), version: count, enabled: z.boolean(), expires_at: z.number() });
export const policySchema = z.object({ memory_id: key, version: count, generation: count, expires_at: z.number(), allow_historical: z.boolean(), conflict_key: z.string().nullable(), conflict_value: z.string().nullable() });
export const pageSchema = z.object({ page: count, has_more: z.boolean(), next_page: count.nullable() });
export type PageInfo = z.infer<typeof pageSchema>;
export const overviewSchema = z.object({
  connections: z.array(connectionSchema).max(1000), memories: z.array(memorySchema).max(10000),
  events: z.array(z.object({ connection_id: key, event_id: key, event_type: z.string().max(64), outcome: z.string().max(32), received_at: z.number(), processing: z.string().max(32), memory_id: key.nullable() })).max(10000),
  grants: z.array(grantSchema).max(10000),
  policies: z.array(policySchema).max(10000),
  jobs: z.array(z.object({ job_id: key, kind: z.string().max(32), state: z.string().max(32), memory_id: key.nullable(), created_at: z.number() })).max(10000),
  pagination: z.object({ connections: pageSchema, events: pageSchema, memories: pageSchema, grants: pageSchema, policies: pageSchema, jobs: pageSchema }).optional(),
});
export type Overview = z.infer<typeof overviewSchema>;
export type Memory = z.infer<typeof memorySchema>;
export type Draft = z.infer<typeof draftSchema>;
export type Connection = z.infer<typeof connectionSchema>;
export const kindLabels = { episode: "故障记录", repair_experience: "修复经验", project_constraint: "项目约束" } as const;
export const stateLabels = { draft: "待确认", confirmed: "已确认", revoked: "已撤回", deleted: "已删除" } as const;
export const projectPath = (project: string) => `/api/memory/projects/${key.parse(project)}`;
export const newRequest = () => crypto.randomUUID();
export class MemoryApiError extends Error { constructor(message: string, readonly status: number) { super(message); } }

/** Bounded JSON transport. Never render server exception text or persist credentials. */
export async function memoryRequest(path: string, options?: { method?: "POST" | "PATCH"; body?: unknown; signal?: AbortSignal }): Promise<unknown> {
  const response = await fetch(path, { cache: "no-store", method: options?.method ?? "GET", signal: AbortSignal.any([AbortSignal.timeout(15000), ...(options?.signal ? [options.signal] : [])]), ...(options?.method ? { headers: { "Content-Type": "application/json", "X-AWB-Local": "1" }, body: JSON.stringify(options.body ?? {}) } : {}) });
  if (!response.ok) {
    await response.body?.cancel();
    throw new MemoryApiError(response.status === 409 ? "记录已变化或操作结果待核对，请刷新后再操作。" : response.status === 403 ? "当前来源未获授权或连接已暂停。" : response.status === 422 || response.status === 400 ? "输入不符合要求，请检查标识、有效期与内容长度。" : "本机服务暂不可用。写入结果可能未知，请先刷新核对，不要重复提交。", response.status);
  }
  if (!response.headers.get("content-type")?.startsWith("application/json") || !response.body) throw new Error("服务响应无法识别，请刷新核对状态。");
  const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let total = 0;
  try { while (true) { const part = await reader.read(); if (part.done) break; total += part.value.length; if (total > 2 * 1024 * 1024) { await reader.cancel(); throw new Error("记录过多，已停止读取。请在命令行按项目检查。"); } chunks.push(part.value); } } finally { reader.releaseLock(); }
  const bytes = new Uint8Array(total); let offset = 0; for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  try { return JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)); } catch { throw new Error("服务响应无法识别，请刷新核对状态。"); }
}
export async function loadProjects(signal?: AbortSignal, page = 0) {
  return z.object({ projects: z.array(z.object({ project_id: key, created_at: z.number() })).max(1000), pagination: pageSchema.optional() }).parse(await memoryRequest(`/api/memory/projects${page ? `?page=${page}` : ""}`, { signal }));
}
export async function loadOverview(project: string, signal?: AbortSignal, pages: Record<string, number> = {}) { const params = new URLSearchParams(Object.entries(pages).filter(([, value]) => value > 0).map(([name, value]) => [`${name}_page`, String(value)])); return overviewSchema.parse(await memoryRequest(`${projectPath(project)}/overview${params.size ? `?${params}` : ""}`, { signal })); }
export function safeMessage(error: unknown) { return error instanceof z.ZodError ? "页面与服务数据约定不一致，请重启服务后刷新。" : error instanceof TypeError || error instanceof DOMException ? "请求未完成，写入结果可能未知。请刷新核对状态，不要重复提交。" : error instanceof Error ? error.message : "请求未完成，请刷新核对状态。"; }
export function dateLabel(seconds: number) { return new Date(seconds * 1000).toLocaleString("zh-CN"); }

export const memoryBindingSchema = z.strictObject({ project_id: key, connection_id: key, allow_historical: z.boolean() });
export type MemoryBinding = z.infer<typeof memoryBindingSchema>;
