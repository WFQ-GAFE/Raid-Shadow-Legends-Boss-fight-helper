export function DecisionRows({ rows }: { rows: unknown }) {
  if (!Array.isArray(rows)) return null
  return <div className="decision-rows">{rows.filter((row) => row && typeof row === 'object').map((row, index) => {
    const reserved = row.reason === 'skill_reserved_for_trial_rule' || row.outcome === 'reserved_for_trial'
    const checks = Array.isArray(row.conditions) ? row.conditions.filter((check: unknown) => check && typeof check === 'object') : []
    const owners = Array.isArray(row.reservedByRules) ? row.reservedByRules.filter((name: unknown) => typeof name === 'string') : []
    const name = typeof row.name === 'string' ? row.name : typeof row.rule === 'string' ? row.rule : '规则'
    return <article key={index}>
      <strong data-i18n-skip>{typeof row.index === 'number' ? `${row.index}. ` : ''}{name}</strong>
      <span>{reserved ? '为试炼专用规则保留技能' : row.outcome === 'selected' ? '已选中' : row.outcome === 'unavailable' ? '条件满足，但技能或目标不可用' : '条件未满足'}</span>
      <small data-i18n-skip>{reserved ? owners.join(' · ') : checks.map((check: { passed?: unknown; key?: unknown }) => `${check.passed === true ? '✓' : '×'} ${typeof check.key === 'string' ? check.key : '条件'}`).join(' · ')}</small>
    </article>
  })}</div>
}
