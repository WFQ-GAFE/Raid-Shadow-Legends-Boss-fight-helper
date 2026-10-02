import { backendText, tr } from './i18n'

export function DecisionRows({ rows }: { rows: unknown }) {
  if (!Array.isArray(rows)) return null
  return <div className="decision-rows">{rows.filter((row) => row && typeof row === 'object').map((row, index) => {
    const reserved = row.reason === 'skill_reserved_for_trial_rule' || row.outcome === 'reserved_for_trial'
    const checks = Array.isArray(row.conditions) ? row.conditions.filter((check: unknown) => check && typeof check === 'object') : []
    const owners = Array.isArray(row.reservedByRules) ? row.reservedByRules.filter((name: unknown) => typeof name === 'string') : []
    const name = typeof row.name === 'string' ? row.name : typeof row.rule === 'string' ? row.rule : tr('decision.rule')
    return <article key={index}>
      <strong data-i18n-skip>{typeof row.index === 'number' ? `${row.index}. ` : ''}{backendText(name, undefined, false)}</strong>
      <span>{reserved ? tr('decision.skillReservedForTrialRules') : row.outcome === 'selected' ? tr('decision.selected') : row.outcome === 'unavailable' ? tr('decision.conditionsMatchButTheSkill') : tr('decision.conditionsNotMet')}</span>
      <small data-i18n-skip>{reserved ? owners.map((owner: string) => backendText(owner, undefined, false)).join(' · ') : checks.map((check: { passed?: unknown; key?: unknown }) => `${check.passed === true ? '✓' : '×'} ${typeof check.key === 'string' ? check.key : tr('decision.condition')}`).join(' · ')}</small>
    </article>
  })}</div>
}
