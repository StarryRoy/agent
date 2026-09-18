import * as api from './api.js';

const $ = id => document.getElementById(id);
let current = null;
let view = null;
let busy = false;
let stopStream = null;
let generation = 0;
let timelineCount = 0;
let eventIds = new Set();
const statusLabels = { running: '执行中', approval_required: '等待审批', completed: '处理完成', error: '发生错误', interrupted: '执行中断' };
const names = {
  request: '采购需求', product: '产品', product_code: '产品编号', product_name: '产品名称',
  quantity: '数量', budget: '需求预算', deadline: '交付期限', department: '部门',
  department_code: '部门编号', priority: '优先级', excluded_suppliers: '排除的供应商',
  recommended_purchase_quantity: '建议采购数量', available_quantity: '可用库存',
  available_inventory: '可用库存', current_stock: '当前库存', locked_quantity: '锁定数量',
  in_transit_quantity: '在途数量', safety_stock: '安全库存', forecast_consumption: '预测消耗',
  shortage: '采购缺口', candidate_suppliers: '候选供应商', supplier_code: '供应商编号',
  supplier_name: '供应商', supplier_id: '供应商 ID', name: '名称', code: '编号',
  unit_price: '单价', total_cost: '总金额', total_amount: '总金额', amount: '金额',
  plan_id: '方案编号', allocations: '供应分配', plans: '采购方案', currency: '币种',
  recommended_plan: '推荐方案', alternative_plans: '备选方案', feasible: '是否可行',
  within_budget: '预算内', available_budget: '可用预算', total_budget: '部门总预算',
  used_budget: '已用预算', reserved_budget: '已占用预算', requested_budget: '需求预算',
  risk_level: '风险等级', risk_score: '风险评分', risks: '风险项', reasons: '原因',
  delivery_days: '交付天数', lead_time_days: '供货周期（天）', delivery_date: '交付日期',
  quality_score: '质量评分', delivery_score: '交付评分', status: '状态',
  approval_status: '审批状态', execution_status: '执行状态', executed_actions: '执行记录',
  action: '操作', result: '结果', summary: '摘要', warnings: '提示', missing_fields: '待补充信息',
  replan_count: '重规划次数', notes: '说明', reason: '原因', constraints: '约束条件',
  min_order_quantity: '起订量', capacity: '产能', success: '成功', order_id: '订单编号',
  purchase_request_id: '采购申请编号', purchase_orders: '采购订单', evidence: '事实依据',
  expected_delivery_date: '期望交付日期', latest_delivery_date: '最晚交付日期',
  other_constraints: '其他约束', preferred_suppliers: '偏好供应商',
  quality_requirements: '质量要求', sku: '产品 SKU', product_id: '产品 ID',
  current_available_quantity: '当前可用库存', estimated_shortfall: '预计缺口',
  projected_usable_quantity: '预计可用于本次需求', inventory_risk: '库存风险',
  conclusion: '结论', facts: '事实依据', fallback_basis: '替代依据', replan_reason: '重规划原因',
  budget_total: '部门预算总额', approved_not_executed: '已审批未执行', user_budget: '用户预算上限',
  effective_available_budget: '本次可用预算', estimated_occupation: '最低可行方案预计占用',
  over_budget_amount: '超预算金额', adjustment_room: '调整空间', budget_risk: '预算风险',
  average_unit_price: '平均单价', conditional: '是否附带条件', max_lead_time_days: '最长交付天数',
  meets_deadline: '满足交期', meets_quantity: '满足数量', risk_items: '风险项',
  within_user_budget: '符合用户预算',
  id: '编号', count: '数量', request_no: '采购申请单号', session_id: 'Session ID',
};
const technicalFields = new Set(['queries', 'query', 'schema_evidence', 'subtask', 'analysis_strategy', 'selected_plan_index']);
const values = { pending: '等待审批', approved: '已批准', rejected: '已拒绝', success: '成功',
  failed: '失败', blocked: '已阻断', not_required: '无需执行', not_started: '未开始',
  awaiting_approval: '等待审批', started: '已开始', info: '信息', loaded: '已加载', unloaded: '已卸载',
  error: '错误', low: '低', medium: '中', high: '高', ordered: '已下单',
  agent: 'Agent 分析', tool: '工具调用', middleware: '流程检查', database: '数据库查询',
  create_purchase_request: '创建采购申请', create_purchase_orders: '创建采购订单',
  reserve_budget: '预留采购预算', update_purchase_status: '更新采购状态',
  record_approval: '记录审批结果', write_operation_log: '写入操作日志' };
const agentLabels = {
  procurement_main_agent: '采购主 Agent', requirement_agent: '需求 Agent',
  inventory_agent: '库存 Agent', supplier_agent: '供应商 Agent',
  pricing_agent: '定价 Agent', budget_agent: '预算 Agent',
  risk_agent: '风险 Agent', execution_agent: '执行 Agent',
};
const toolLabels = {
  parse_requirement: '解析采购需求', calculate_inventory: '计算库存缺口',
  calculate_suppliers: '筛选供应商', calculate_pricing: '计算价格方案',
  calculate_budget: '核验预算', calculate_risk: '评估采购风险',
  execute_procurement_plan: '执行采购方案', execute_query: '查询业务数据',
  supplier_status: '查询供应商状态', load_skill: '加载业务 Skill',
  unload_skill: '卸载业务 Skill', agent_harness_structured_response: '整理结构化结果',
};
const eventLabels = {
  operation: '用户操作', 'agent.start': 'Agent 开始', 'agent.end': 'Agent 完成',
  'subagent.start': '专业 Agent 开始', 'subagent.end': '专业 Agent 完成',
  'tool.start': '工具开始', 'tool.end': '工具完成', 'model.start': '模型开始分析',
  'model.end': '模型完成分析', 'middleware.hook': '流程检查', 'skill.load': '加载业务 Skill',
  'skill.unload': '卸载业务 Skill', 'database.start': '数据库查询开始',
  'database.end': '数据库查询完成', 'mcp.tool': '外部服务调用', 'plan.replan': '方案重新规划',
  approval_required: '等待人工审批', plan_update: '计划更新', final: '分析返回',
  text_delta: '模型输出', operation_error: '执行错误',
};
const hookLabels = {
  before_model: '模型调用前检查', after_model: '模型调用后检查',
  before_agent: 'Agent 执行前检查', after_agent: 'Agent 执行后检查',
  before_tool: '工具调用前检查', after_tool: '工具调用后检查',
};
const middlewareLabels = {
  TimeoutMiddleware: '超时控制', CallLimitMiddleware: '调用次数控制',
  RetryMiddleware: '自动重试', ProcurementContextMiddleware: '业务上下文整理',
  ProcurementOrchestrationMiddleware: '依赖与一致性校验',
};

function node(tag, text, className) {
  const el = document.createElement(tag);
  if (text !== undefined) el.textContent = text;
  if (className) el.className = className;
  return el;
}

// Generic structured-data renderer: labels and formatting only, no business rules.
function dataView(value, depth = 0) {
  if (value === null || value === undefined || value === '') return node('span', '—', 'muted');
  if (typeof value !== 'object') {
    const text = typeof value === 'boolean' ? (value ? '是' : '否')
      : typeof value === 'number' ? value.toLocaleString('zh-CN') : (values[value] || value);
    return node('span', String(text));
  }
  if (!Object.keys(value).length) return node('p', '暂无数据', 'muted');
  if (depth > 5) return node('pre', JSON.stringify(value, null, 2));
  if (Array.isArray(value)) {
    const list = node('div', undefined, 'data-list');
    value.forEach((item, index) => {
      const row = node('div', undefined, 'data-item');
      if (typeof item === 'object' && item !== null) row.append(node('span', String(index + 1).padStart(2, '0'), 'item-index'));
      row.append(dataView(item, depth + 1));
      list.append(row);
    });
    return list;
  }
  const dl = node('dl', undefined, 'data-fields');
  for (const [key, item] of Object.entries(value)) {
    if (key.startsWith('simulate_') || technicalFields.has(key)) continue;
    const group = node('div', undefined, typeof item === 'object' && item !== null ? 'field complex' : 'field');
    group.append(node('dt', names[key] || key));
    const dd = node('dd'); dd.append(dataView(item, depth + 1)); group.append(dd); dl.append(group);
  }
  return dl;
}

const narrativeTranslations = [
  [/\bthe procurement plan is ready for approval\b/gi, '采购方案已准备好，等待审批'],
  [/\bthe procurement plan is waiting for approval\b/gi, '采购方案正在等待审批'],
  [/\bthe procurement plan was approved\b/gi, '采购方案已批准'],
  [/\bthe procurement plan was rejected\b/gi, '采购方案已拒绝'],
  [/\bno feasible procurement plan was found\b/gi, '未找到可行的采购方案'],
  [/\binventory analysis completed\b/gi, '库存分析已完成'],
  [/\bsupplier analysis completed\b/gi, '供应商分析已完成'],
  [/\bpricing analysis completed\b/gi, '定价分析已完成'],
  [/\bbudget analysis completed\b/gi, '预算分析已完成'],
  [/\brisk analysis completed\b/gi, '风险分析已完成'],
  [/\bexecution completed successfully\b/gi, '执行成功完成'],
  [/\bexecution failed\b/gi, '执行失败'],
  [/\bplease wait\b/gi, '请等待'],
  [/\bprocurement plan\b/gi, '采购方案'],
  [/\bprocurement request\b/gi, '采购申请'],
  [/\binventory analysis\b/gi, '库存分析'],
  [/\bpricing analysis\b/gi, '定价分析'],
  [/\bsupplier risk\b/gi, '供应商风险'],
  [/\bdelivery deadline\b/gi, '交付期限'],
  [/\bdelivery date\b/gi, '交付日期'],
  [/\bapproval required\b/gi, '等待审批'],
  [/\bwaiting for approval\b/gi, '等待审批'],
  [/\bno feasible plan\b/gi, '暂无可行方案'],
  [/\bquery failed\b/gi, '查询失败'],
  [/\bdata unavailable\b/gi, '数据不可用'],
  [/\bsupplier\b/gi, '供应商'],
  [/\binventory\b/gi, '库存'],
  [/\bpricing\b/gi, '定价'],
  [/\bcost\b/gi, '成本'],
  [/\bbudget\b/gi, '预算'],
  [/\bdelivery\b/gi, '交付'],
  [/\bstatus\b/gi, '状态'],
  [/\bsuccess\b/gi, '成功'],
  [/\bfailed\b/gi, '失败'],
];

function localizeNarrative(text) {
  return narrativeTranslations.reduce((result, [pattern, replacement]) => (
    result.replace(pattern, replacement)
  ), String(text));
}

function inlineMarkdown(text) {
  const fragment = document.createDocumentFragment();
  const pattern = /(\*\*|__)(.+?)\1/g;
  let cursor = 0;
  let match;
  const appendText = value => {
    if (value) fragment.append(document.createTextNode(localizeNarrative(value)));
  };
  while ((match = pattern.exec(String(text))) !== null) {
    appendText(String(text).slice(cursor, match.index));
    const strong = node('strong');
    strong.textContent = localizeNarrative(match[2]);
    fragment.append(strong);
    cursor = match.index + match[0].length;
  }
  appendText(String(text).slice(cursor));
  return fragment;
}

// Render the user-facing narrative without injecting HTML. Technical JSON remains untouched below.
function renderMarkdown(value, className = 'markdown-content') {
  const container = node('div', undefined, className);
  const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
  let paragraph = [];
  let list = null;
  let listType = null;

  const flushParagraph = () => {
    if (!paragraph.length) return;
    const content = node('p');
    paragraph.forEach((line, index) => {
      if (index) content.append(document.createElement('br'));
      content.append(inlineMarkdown(line));
    });
    container.append(content);
    paragraph = [];
  };
  const closeList = () => {
    if (list) container.append(list);
    list = null;
    listType = null;
  };

  lines.forEach(line => {
    const trimmed = line.trim();
    if (!trimmed) {
      flushParagraph();
      closeList();
      return;
    }
    const heading = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushParagraph();
      closeList();
      const headingNode = node(heading[1].length <= 2 ? 'h4' : 'h5');
      headingNode.append(inlineMarkdown(heading[2]));
      container.append(headingNode);
      return;
    }
    const unordered = trimmed.match(/^[-*+]\s+(.+)$/);
    const ordered = trimmed.match(/^\d+[.)]\s+(.+)$/);
    if (unordered || ordered) {
      flushParagraph();
      const nextType = unordered ? 'ul' : 'ol';
      if (listType !== nextType) closeList();
      if (!list) {
        list = node(nextType);
        listType = nextType;
      }
      const item = node('li');
      item.append(inlineMarkdown((unordered || ordered)[1]));
      list.append(item);
      return;
    }
    closeList();
    paragraph.push(line);
  });
  flushParagraph();
  closeList();
  return container;
}

function resultGroup(title, content, className = '') {
  const group = node('section', undefined, `result-group ${className}`.trim());
  group.append(node('h4', title));
  group.append(content);
  return group;
}

function statusTile(label, value) {
  const tile = node('div', undefined, 'result-status');
  tile.append(node('span', label));
  tile.append(node('strong', values[value] || value || '暂无'));
  return tile;
}

function renderFinalResult(data, next) {
  const result = node('div', undefined, 'result-layout');
  const conclusion = node('section', undefined, 'result-conclusion');
  conclusion.append(node('p', '处理结论', 'result-kicker'));
  conclusion.append(renderMarkdown(data.summary || next.message || '采购结果已更新。', 'markdown-content result-summary'));
  result.append(conclusion);

  const statuses = node('div', undefined, 'result-status-grid');
  statuses.append(
    statusTile('审批状态', data.approval_status),
    statusTile('执行状态', data.execution_status),
  );
  result.append(statuses);

  const notices = [];
  if (data.missing_fields?.length) notices.push(['待补充信息', data.missing_fields, 'notice']);
  if (data.warnings?.length) notices.push(['提示信息', data.warnings, 'notice']);
  if (notices.length) {
    const noticeList = node('div', undefined, 'result-notices');
    notices.forEach(([title, value, className]) => {
      const notice = node('div', undefined, `notice ${className}`);
      notice.append(node('strong', title), dataView(value));
      noticeList.append(notice);
    });
    result.append(resultGroup('提示信息', noticeList));
  }

  const actions = data.executed_actions || [];
  result.append(resultGroup(
    '执行记录',
    actions.length ? dataView(actions) : node('p', '当前没有采购写入记录。', 'muted'),
    'result-actions',
  ));
  return result;
}

function eventContext(event) {
  const payload = event.data || event.metadata || {};
  const inner = payload.data || payload.metadata || payload;
  const type = event.event_type === 'trace' ? payload.event_type : event.event_type;
  return { payload, inner, type };
}

function technicalLabel(container, label, identifier, className = '') {
  const wrapper = node('span', undefined, `technical-label ${className}`.trim());
  wrapper.append(node('span', label));
  if (identifier && identifier !== label) wrapper.append(node('code', identifier));
  container.append(wrapper);
}

function eventDetails(event, payload, inner, type) {
  const details = node('div', undefined, 'event-details');
  const facts = node('dl', undefined, 'event-facts');
  const fact = (label, value) => {
    if (value === undefined || value === null || value === '') return;
    const row = node('div', undefined, 'event-fact');
    row.append(node('dt', label), node('dd', values[value] || String(value)));
    facts.append(row);
  };
  fact('状态', event.status || payload.status || inner.status);
  fact('处理阶段', hookLabels[inner.hook] || inner.hook);
  fact('调用目的', values[inner.purpose] || inner.purpose);
  if (event.duration_ms !== undefined && event.duration_ms !== null) {
    fact('耗时', `${Number(event.duration_ms).toLocaleString('zh-CN', { maximumFractionDigits: 0 })} ms`);
  }
  if (inner.result_status) fact('结果状态', inner.result_status);
  if (facts.children.length) details.append(facts);

  const message = inner.message || payload.message || inner.content || payload.content;
  if (typeof message === 'string' && message.trim()) {
    details.append(renderMarkdown(message, 'markdown-content event-message'));
  }
  if (event.error || payload.error || inner.error) {
    details.append(node('p', localizeNarrative(String(event.error || payload.error || inner.error)), 'event-error'));
  }

  const raw = node('details', undefined, 'event-raw');
  raw.append(node('summary', '查看原始事件（技术信息）'));
  raw.append(node('pre', JSON.stringify(event, null, 2)));
  details.append(raw);
  return details;
}

function error(message = '') { $('error').hidden = !message; $('error').textContent = message; }
function controls() {
  const running = busy || view?.status === 'running';
  $('submit').disabled = running;
  $('restore-id').disabled = running;
  $('restore-form').querySelector('button').disabled = running;
  $('approve').disabled = running || view?.status !== 'approval_required';
  $('reject').disabled = $('approve').disabled;
  $('modify').disabled = running || !['approval_required', 'completed'].includes(view?.status);
  $('modify-text').disabled = $('modify').disabled;
}

function render(next) {
  view = next;
  $('session-id').textContent = next.session_id;
  $('status').textContent = statusLabels[next.status] || next.status;
  $('status').className = `pill ${next.status}`;
  $('session-message').textContent = next.message;
  const data = next.data || {};
  $('summary').className = 'summary-content';
  $('summary').replaceChildren();
  $('summary').append(node('p', '当前结论', 'result-kicker'));
  $('summary').append(renderMarkdown(data.summary || next.message || '采购结果已更新。', 'markdown-content summary-narrative'));
  if (data.request && Object.keys(data.request).length) {
    const selected = Object.fromEntries(['product', 'quantity', 'budget', 'department_code',
      'expected_delivery_date', 'latest_delivery_date', 'excluded_suppliers'].filter(key => {
      const value = data.request[key];
      return value !== undefined && value !== null && (!Array.isArray(value) || value.length);
    }).map(key => [key, data.request[key]]));
    $('summary').append(dataView(selected));
  }
  for (const key of ['missing_fields', 'warnings']) {
    if (data[key]?.length) {
      const note = node('div', undefined, 'notice');
      note.append(node('strong', names[key]), dataView(data[key])); $('summary').append(note);
    }
  }
  const sections = [
    ['inventory_analysis', '库存分析'], ['candidate_suppliers', '候选供应商'],
    ['pricing_analysis', '价格方案'], ['budget_analysis', '预算分析'],
    ['risk_analysis', '风险分析'], ['recommended_plan', '推荐方案'],
    ['alternative_plans', '备选方案'],
  ];
  $('analysis-grid').replaceChildren();
  sections.forEach(([key, title]) => {
    const card = node('section', undefined, `card analysis-card ${key === 'recommended_plan' ? 'recommended' : ''}`);
    const heading = node('div', undefined, 'analysis-card-heading');
    heading.append(node('h2', title));
    if (data[key]?.status) {
      const status = String(data[key].status);
      heading.append(node('span', values[status] || status, `analysis-status status-${status.replace(/[^a-z0-9_-]/gi, '-')}`));
    }
    card.append(heading);
    if (key === 'recommended_plan') card.append(dataView(data[key]));
    else {
      const details = node('details');
      if (data[key]?.conclusion) {
        card.append(renderMarkdown(data[key].conclusion, 'markdown-content analysis-conclusion'));
      }
      details.append(node('summary', '查看结构化分析'), dataView(data[key])); card.append(details);
    }
    $('analysis-grid').append(card);
  });
  $('approval-hint').textContent = next.status === 'approval_required'
    ? '方案已就绪，尚未执行。批准后将恢复 Agent 并创建采购记录；修改将使原审批方案失效。'
    : next.status === 'running' ? 'Agent 正在执行，操作按钮暂时锁定。'
    : `审批状态：${values[data.approval_status] || data.approval_status || '暂无'}。可在下方补充或修改需求。`;
  $('result').replaceChildren(renderFinalResult(data, next));
  const active = next.status === 'running' ? 1 : next.status === 'approval_required' ? 2 : 3;
  [...$('flow').children].forEach((el, i) => el.classList.toggle('active', i === active));
  controls();
}

function addEvent(event) {
  const id = event.id ? `${current}:${event.id}` : null;
  if (id && eventIds.has(id)) return;
  if (id) eventIds.add(id);
  if (!timelineCount) $('timeline').replaceChildren();
  timelineCount++;
  const { payload, inner, type } = eventContext(event);
  const label = eventLabels[type] || eventLabels[String(type || '').replace(/_/g, '.')] || '执行事件';
  const item = node('li');
  item.append(node('time', new Date(event.timestamp || Date.now()).toLocaleTimeString('zh-CN', { hour12: false })));
  item.append(node('strong', label));

  const info = node('div', undefined, 'event-detail');
  const agent = event.agent_name || payload.agent_name || inner.agent_name;
  const target = inner.name || inner.operation || inner.tool_name;
  if (agent) technicalLabel(info, agentLabels[agent] || 'Agent', agent, 'event-agent');
  if (target && target !== agent) {
    if (info.children.length) info.append(node('span', ' · ', 'event-separator'));
    technicalLabel(
      info,
      toolLabels[target] || agentLabels[target] || middlewareLabels[target] || target,
      target,
      'event-target',
    );
  }
  if (!info.children.length && inner.message) info.append(node('span', inner.message));
  if (info.children.length) item.append(info);
  item.append(eventDetails(event, payload, inner, type));
  $('timeline').append(item);
  while ($('timeline').children.length > 120) $('timeline').firstChild.remove();
  $('event-count').textContent = timelineCount;
  $('timeline').scrollTop = $('timeline').scrollHeight;
}

async function observability() {
  const id = current;
  const [metricsResult, traceResult] = await Promise.allSettled([
    api.request('/metrics'), id ? api.trace(id) : Promise.resolve(null),
  ]);
  if (metricsResult.status === 'fulfilled') {
    const metrics = metricsResult.value.metrics;
    $('metrics-json').textContent = JSON.stringify(metrics, null, 2);
    $('metrics').replaceChildren();
    [['agent_calls', 'Agent 调用'], ['database_calls', '数据库调用'], ['replan_count', '重规划'], ['errors', '错误事件']].forEach(([key, label]) => {
      const tile = node('div', undefined, 'metric'); tile.append(node('strong', metrics[key] ?? '—'), node('span', label)); $('metrics').append(tile);
    });
  }
  if (traceResult.status === 'fulfilled' && traceResult.value && current === id) {
    const trace = traceResult.value;
    $('trace-json').textContent = JSON.stringify(trace.events, null, 2);
    $('trace-count').textContent = `(${trace.events.length})`;
    $('trace-note').textContent = trace.truncated ? '仅展示最近 500 条；完整记录见后端 data/traces.jsonl。' : '该 Session 的持久化执行事件。';
  }
  if (metricsResult.status === 'rejected' || traceResult.status === 'rejected') error('可观测信息获取失败，请检查后端连接后刷新。');
}

async function load(id, reset = true) {
  stopStream?.();
  const token = ++generation;
  current = id;
  view = null;
  controls();
  localStorage.setItem('procurement.session', id);
  if (reset) {
    timelineCount = 0; eventIds = new Set(); $('timeline').replaceChildren(); $('event-count').textContent = '0';
  }
  const state = await api.session(id);
  if (generation !== token) return;
  render(state);
  await observability();
  if (generation !== token) return;
  if (reset && state.status !== 'running') {
    const history = await api.trace(id);
    if (generation !== token) return;
    history.events.slice(-120).forEach(addEvent);
  }
  stopStream = api.observe(id, {
    progress: event => { if (generation === token) addEvent(event); },
    snapshot: state => { if (generation === token) render(state); },
    reset: () => { if (generation === token) observability(); },
    done: () => { if (generation === token) { error(); observability(); } },
    error: () => { if (generation === token) error('实时连接暂时中断，正在自动重连。Agent 会继续处理，请勿重复提交。'); },
  });
}

async function perform(action) {
  busy = true; error(); controls();
  try { await action(); }
  catch (err) { error(err.message || '无法连接后端，请检查服务是否已启动。'); }
  finally { busy = false; controls(); }
}

$('example').onclick = () => { $('request-text').value = '下个月需要采购500台设备，预算80万，月底前必须到货。'; $('request-text').focus(); };
$('request-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  const created = await api.submit($('request-text').value); await load(created.session_id);
}); };
$('restore-form').onsubmit = event => { event.preventDefault(); perform(() => load($('restore-id').value.trim())); };
for (const action of ['approve', 'reject']) $(action).onclick = () => perform(async () => { await api.act(current, action); await load(current, false); });
$('modify-form').onsubmit = event => { event.preventDefault(); perform(async () => {
  await api.act(current, 'modify', $('modify-text').value); $('modify-text').value = ''; await load(current, false);
}); };
$('refresh-metrics').onclick = () => perform(observability);
$('docs-link').href = `${api.API_BASE}/docs`;
async function initialize() {
  try {
    const health = await api.request('/health');
    $('connection').textContent = health.mode === 'deterministic_demo' ? '● 离线演示模型' : '● 已连接配置模型';
    $('connection').classList.add('online');
    const saved = localStorage.getItem('procurement.session');
    if (saved) { $('restore-id').value = saved; await perform(() => load(saved)); }
    else await observability();
  } catch { $('connection').textContent = '● 后端未连接'; error('请先启动 FastAPI 后端，再刷新页面。默认地址：http://127.0.0.1:8000'); }
}
initialize();
