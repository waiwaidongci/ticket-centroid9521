import {datasetField, feedback, field} from '../components/common/fields.js';
import {empty, metrics, panel, pill} from '../components/common/panels.js';
import {table} from '../components/common/table.js';
import {STATUS} from '../constants/enums.js';
import {handler, poll} from '../hooks/action.js';
import {session} from '../stores/session.js';
import {workspace} from '../stores/workspace.js';
import {button, el, h, link, p} from '../utils/dom.js';
import {date, value} from '../utils/format.js';

function permutationRequest(data, spec) {
  return {dataset_id:Number(data.get('dataset_id')), name:data.get('name'),
    params:Object.fromEntries(spec.params.map(param=>[param.key,data.get(param.key)]))};
}

function progressBar(item) {
  const percent = `${(item.progress * 100).toFixed(1)}%`;
  return el('div', {class:'progress', 'aria-live':'polite'},
    el('div', {class:'progress-track'}, el('div', {class:'progress-fill', style:{width:percent}})),
    p(`${item.settled_combinations} / ${item.total_combinations} 组合已结清（${percent}）`, 'subtle'));
}

function ticketTable(job) {
  const columns = [
    {key:'ticket_id', label:'来信编号'}, {key:'session', label:'演出场次'}, {key:'actual', label:'人工归口'},
    {key:'model_destination', label:'分流去向'}, {key:'baseline_destination', label:'多数类基线去向'},
  ];
  return table(columns, job.tickets, {caption:'开跑时固定的留出件场次与两套去向', empty:'暂无快照。', pageSize:10});
}

export async function permutationPage(route, refresh) {
  const {spec, datasets} = workspace;
  const jobs = await workspace.permutations();
  const selectedId = route.id || jobs[0]?.id;
  const error = feedback();
  const submit = button('启动场次核对', {type:'submit', class:'primary', 'data-testid':'start-permutation'});
  const form = el('form', {class:'fields', 'data-testid':'permutation-form',
    onSubmit:handler(submit, error, async event => {
      const item = await workspace.startPermutation(
        permutationRequest(new FormData(event.target), spec));
      location.hash = `#permutation/${item.id}`;
    })},
    datasetField(datasets, route.id),
    field({key:'name', label:'核对名称', type:'string', max_length:120}, '样本场次核对'),
    el('div', {class:'form-grid'}, spec.params.map(param=>field(param))),
    el('div', {class:'actions'}, submit), error);

  const launch = panel(spec.permutation_title || '场次显著性核对',
    p('给每封留出件补上演出场次后启动。任一封留出件缺场次将直接拒绝，不会产生后台任务。', 'subtle'),
    session.canWrite ? (datasets.length ? form : empty('请先导入数据。', '#inputs', '前往导入'))
      : p('只读角色可以查看核对记录与结果。', 'empty'));

  const jobList = panel('核对记录', table([
    {label:'核对', render:item=>link(item.name, `#permutation/${item.id}`)},
    {label:'状态', render:item=>pill(STATUS[item.status], item.status)},
    {label:'结清进度', render:item=>`${item.settled_combinations} / ${item.total_combinations}`},
    {label:'命中组合', render:item=>value(item.hit_combinations)},
    {label:'时间', render:item=>date(item.created_at)},
  ], jobs, {caption:'核对记录', empty:'启动后将在此显示分批结清进度。'}));

  let detailPanel;
  if (!selectedId) {
    detailPanel = panel('核对结果', empty('还没有可查看的场次核对。', '#run', '前往分流验收'));
  } else {
    const job = await workspace.getPermutation(selectedId);
    const finished = item => item.status === 'complete' || item.status === 'failed';
    if (!finished(job)) {
      const original = location.hash;
      const status = el('p', {role:'status'}, '后台正在按批结清组合，完成后自动展示。');
      poll(()=>workspace.getPermutation(selectedId), finished, ()=>location.hash===original)
        .then(result=>{if (result && status.isConnected && location.hash===original) refresh();
          else if (status.isConnected) status.textContent='仍在执行，请点击刷新检查。';})
        .catch(err=>{if (status.isConnected) status.textContent=err.message;});
      detailPanel = panel(job.name, pill(STATUS[job.status], job.status), progressBar(job), status,
        button('刷新', {class:'quiet', onClick:refresh}));
    } else if (job.status === 'failed') {
      detailPanel = panel(job.name, pill(STATUS[job.status], job.status),
        p(job.error || '核对失败', 'feedback'), link('返回调整批次', '#inputs', {class:'quiet'}));
    } else {
      const result = job.result;
      detailPanel = el('section', {class:'analysis', 'data-testid':'permutation-complete'},
        panel(job.name,
          el('div', {class:'result-head'}, p(spec.permutation_title || '场次核对', 'eyeline'),
            h(2, result.headline), p(result.note, 'intro-copy'),
            el('div', {class:'actions'}, pill(STATUS[job.status], job.status)))),
        metrics(result.metrics),
        panel('结清进度', progressBar(job),
          p(`开跑参数：词表上限 ${value(job.params.max_terms)}，最低相似度 ${value(job.params.min_similarity)}；`
            + `完成于 ${date(job.finished_at)}。`, 'subtle')),
        panel('留出件场次与两套去向', ticketTable(job)),
      );
    }
  }

  return el('div', {class:'two-col'},
    el('div', {class:'wide-panel'}, launch, jobList, detailPanel),
    el('aside', {class:'sidebar-panel'},
      panel('核对口径', el('ul', {class:'method-list'},
        (spec.permutation_methodology || []).map(text=>el('li', {}, text))))));
}
