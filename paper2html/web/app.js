'use strict';
const $ = id => document.getElementById(id);
let csrf = '', polling = 0;
let currentJob = null;
const labels = {queued:'排队中',running:'处理中',completed:'已完成',failed:'处理失败',duplicate:'发现已有任务'};
async function request(path, options) {
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '请求失败');
  return data;
}
function link(label, href, target) {
  const a = document.createElement('a'); a.textContent = label; a.href = href;
  if (target) { a.target = '_blank'; a.rel = 'noopener'; }
  $('downloads').append(a);
}
async function poll(id, generation) {
  if (generation !== polling) return;
  try {
    const job = await request('/api/jobs/' + id);
    if (generation !== polling) return;
    $('empty').hidden = true; $('progress').hidden = false;
    $('status').textContent = labels[job.status] || job.status;
    $('stage').textContent = job.stage;
    currentJob = job;
    $('job-label').textContent = job.filename + ' · ' + job.model + (job.endpoint ? ' · ' + job.endpoint : '') + ' · ' + job.id;
    $('resume-job').hidden = job.status !== 'failed' || !job.fingerprint;
    const started = Date.parse(job.request_started_at);
    const waiting = job.request_state === '等待 API 响应' && Number.isFinite(started);
    $('request-status').textContent = [job.chunks_total ? `已完成阅读 ${job.chunks_completed}/${job.chunks_total} 块` : '',
      job.request_state || '', waiting ? `等待 ${Math.max(0, Math.floor((Date.now()-started)/1000))} 秒 · 请求尝试 ${job.attempt}` : '',
      job.repair_attempt ? `校验修复 ${job.repair_attempt}/2` : '', job.checkpoint_reused ? '已复用通过校验的检查点' : ''].filter(Boolean).join(' · ');
    $('job-error').textContent = job.error || '';
    $('warnings').replaceChildren(); $('downloads').replaceChildren();
    for (const warning of job.warnings || []) { const li = document.createElement('li'); li.textContent = warning; $('warnings').append(li); }
    const active = ['queued','running'].includes(job.status);
    $('busy').hidden = !active;
    if (job.status === 'completed') {
      const base = '/api/jobs/' + id + '/files/';
      link('阅读文章 ↗', base + 'html', true);
      link('下载完整报告 ZIP', base + 'bundle');
      link('下载 Markdown', base + 'markdown'); link('下载 HTML', base + 'html?download=1');
      link('证据清单', base + 'evidence'); link('校验报告', base + 'validation');
      if (job.publication_source?.source === 'Crossref' && job.publication_source.url?.startsWith('https://api.crossref.org/works/')) {
        link('发表日期来源：Crossref', job.publication_source.url, true);
      }
    }
    if (job.status === 'duplicate') {
      $('job-error').textContent = '已找到相同论文，正在打开已有任务。需要重写时可勾选“重新生成”。';
      location.hash = job.existing_job;
      return;
    }
    const u = job.usage;
    $('usage').textContent = u ? `API 调用 ${u.calls} 次 · 输入 ${u.prompt_tokens} / 输出 ${u.completion_tokens} tokens（服务商返回值）` : '';
    if (active) setTimeout(() => poll(id, generation), 2000);
  } catch (error) {
    $('error').textContent = error.message + '。正在尝试重新连接任务。';
    if (generation === polling) setTimeout(() => poll(id, generation), 5000);
  }
}
function followHash() {
  const id = location.hash.slice(1);
  polling++;
  if (/^[a-f0-9]{32}$/.test(id)) poll(id, polling);
}
$('pdf').addEventListener('change', () => { $('filename').textContent = $('pdf').files[0]?.name || '可搜索的 PDF · 最大 25 MB / 150 页'; });
function configFromForm() {
  return {base_url:$('base_url').value.trim(), model:$('model').value.trim(), api_key:$('api_key').value,
    max_tokens:Number($('max_tokens').value), json_mode:$('json_mode').checked, vision:$('vision').checked,
    metadata_lookup:$('metadata_lookup').checked};
}
async function post(path, body) {
  if (!csrf) csrf = (await request('/api/config')).csrf_token;
  return request(path, {method:'POST', headers:{'Content-Type':'application/json','X-Paper2HTML-Token':csrf}, body:JSON.stringify(body)});
}
$('test-api').addEventListener('click', async () => {
  $('test-api').disabled = true; $('connection-status').textContent = '正在测试连接与 JSON 输出…';
  try {
    const result = await post('/api/test', {config:configFromForm()});
    $('connection-status').textContent = `连接与 JSON 输出通过 · 输入 ${result.usage.prompt_tokens} / 输出 ${result.usage.completion_tokens} tokens。图片输入能力尚未测试。`;
  } catch (error) { $('connection-status').textContent = error.message; }
  finally { $('test-api').disabled = false; }
});
$('resume-job').addEventListener('click', () => {
  $('resume_from').value = currentJob.id;
  $('resume_from').closest('details').open = true;
  $('error').textContent = '已填写续跑 ID。请确认相同 PDF、接口、模型和高级选项，重新提供密钥后点击开始阅读。';
});
async function refreshJobs() {
  try {
    const data = await request('/api/jobs'); $('recent-jobs').replaceChildren();
    for (const job of data.jobs) {
      const li = document.createElement('li'), a = document.createElement('a');
      a.href = '#' + job.id;
      a.textContent = `${new Date(job.created_at).toLocaleString()} · ${labels[job.status]} · ${job.model} · ${job.endpoint || '旧任务'} · ${job.stage}`;
      li.append(a); $('recent-jobs').append(li);
    }
  } catch (error) { $('error').textContent = error.message; }
}
$('refresh-jobs').addEventListener('click', refreshJobs);
$('independent').addEventListener('change', () => { if ($('independent').checked) $('regenerate').checked = false; });
$('regenerate').addEventListener('change', () => { if ($('regenerate').checked) $('independent').checked = false; });
$('form').addEventListener('submit', async event => {
  event.preventDefault(); $('error').textContent = ''; $('submit').disabled = true;
  try {
    const file = $('pdf').files[0];
    if (!file || file.size > 25 * 1024 * 1024) throw new Error('请选择不超过 25 MB 的 PDF');
    const metadata = $('metadata').value.trim() ? JSON.parse($('metadata').value) : {};
    const figure_plan = $('figure_plan').value.trim() ? JSON.parse($('figure_plan').value) : undefined;
    if (!csrf) csrf = (await request('/api/config')).csrf_token;
    const pdf_base64 = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error('无法读取 PDF')); reader.readAsDataURL(file); });
    const config = configFromForm();
    const job = await post('/api/jobs', {filename:file.name,pdf_base64,config,metadata,figure_plan,
      regenerate:$('regenerate').checked, independent:$('independent').checked, resume_from:$('resume_from').value.trim()});
    refreshJobs();
    if (location.hash === '#' + job.id) followHash(); else location.hash = job.id;
  } catch (error) { $('error').textContent = error.message; }
  finally { $('submit').disabled = false; }
});
window.addEventListener('hashchange', followHash);
request('/api/config').then(c => { csrf = c.csrf_token; $('base_url').value = c.defaults?.base_url || ''; $('model').value = c.defaults?.model || ''; document.querySelector('.badge').textContent = '本地工作空间 · v' + c.version; followHash(); refreshJobs(); }).catch(e => { $('error').textContent = e.message; });
