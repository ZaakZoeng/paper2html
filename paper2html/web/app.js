'use strict';
const $ = id => document.getElementById(id);
let csrf = '', polling = 0;
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
    $('job-label').textContent = job.filename + ' · ' + job.model;
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
    $('error').textContent = error.message + '。可刷新页面重新连接任务。';
  }
}
function followHash() {
  const id = location.hash.slice(1);
  polling++;
  if (/^[a-f0-9]{32}$/.test(id)) poll(id, polling);
}
$('pdf').addEventListener('change', () => { $('filename').textContent = $('pdf').files[0]?.name || '可搜索的 PDF · 最大 25 MB / 150 页'; });
$('form').addEventListener('submit', async event => {
  event.preventDefault(); $('error').textContent = ''; $('submit').disabled = true;
  try {
    const file = $('pdf').files[0];
    if (!file || file.size > 25 * 1024 * 1024) throw new Error('请选择不超过 25 MB 的 PDF');
    const metadata = $('metadata').value.trim() ? JSON.parse($('metadata').value) : {};
    const figure_plan = $('figure_plan').value.trim() ? JSON.parse($('figure_plan').value) : undefined;
    if (!csrf) csrf = (await request('/api/config')).csrf_token;
    const pdf_base64 = await new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(',')[1]); reader.onerror = () => reject(new Error('无法读取 PDF')); reader.readAsDataURL(file); });
    const config = {base_url:$('base_url').value.trim(), model:$('model').value.trim(), api_key:$('api_key').value, max_tokens:Number($('max_tokens').value), json_mode:$('json_mode').checked, vision:$('vision').checked};
    const job = await request('/api/jobs', {method:'POST', headers:{'Content-Type':'application/json','X-Paper2HTML-Token':csrf}, body:JSON.stringify({filename:file.name,pdf_base64,config,metadata,figure_plan,regenerate:$('regenerate').checked})});
    if (location.hash === '#' + job.id) followHash(); else location.hash = job.id;
  } catch (error) { $('error').textContent = error.message; }
  finally { $('submit').disabled = false; }
});
window.addEventListener('hashchange', followHash);
request('/api/config').then(c => { csrf = c.csrf_token; $('base_url').value = c.defaults?.base_url || ''; $('model').value = c.defaults?.model || ''; followHash(); }).catch(e => { $('error').textContent = e.message; });
