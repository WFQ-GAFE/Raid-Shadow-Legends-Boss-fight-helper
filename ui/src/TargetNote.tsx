import type { ReactNode } from 'react'
import { AlertTriangle, Ban, Crosshair, HeartPulse, SearchX, Shuffle, User, Users, type LucideIcon } from 'lucide-react'
import { HoverCard } from './HoverCard'
import { useI18n } from './i18n'
import { TARGET_LABEL_KEYS, type TargetReach } from './ruleTargets'

// A rule target warning built from short pieces, the same in the rule editor,
// the rule list, the simulation reports and the stuck cards:
//   [⚠ Invalid target] [skill] [◎ enemies only] · rule picks “Self” · this rule never acts

const REACH_ICONS: Record<TargetReach, LucideIcon> = {
  enemy: Crosshair, ally: Users, otherAlly: Users, self: User, deadAlly: HeartPulse, any: Shuffle, none: Ban, absent: SearchX,
}

export type TargetNoteProps = {
  // never: the skill can never take this target; unavailable: it could not then.
  kind: 'never' | 'unavailable'
  reach: TargetReach
  target: string
  position?: number
  // The champion an allyHeroTypeId target names.
  targetName?: string
  // The skill's icon (the editor and the reports draw their own).
  skill?: ReactNode
  outcome?: 'neverActs' | 'autoFallback'
  extra?: ReactNode
  // False where the line already starts with a warning icon (report findings).
  icon?: boolean
}

export function TargetNote({ kind, reach, target, position, targetName, skill, outcome, extra, icon = true }: TargetNoteProps) {
  const { t } = useI18n()
  const Icon = REACH_ICONS[reach] ?? Ban
  const key = TARGET_LABEL_KEYS[target]
  const label = targetName ?? (key ? t(key, { position: position ?? '?' }) : target)
  return <span className={`target-note ${kind}`}>
    <span className="target-note-head">{icon && <AlertTriangle size={14} />}<b>{t(kind === 'never' ? 'target.never' : 'target.unavailable')}</b></span>
    {skill}
    <span className="target-note-reach"><Icon size={14} />{t(`target.reach.${reach}`)}</span>
    <span className="target-note-part">{t('target.picked', { target: label })}</span>
    {outcome && <span className="target-note-part">{t(`target.${outcome}`)}</span>}
    {extra && <span className="target-note-part">{extra}</span>}
  </span>
}

// A small tag (rule list, rules table, action log) with the full notes on hover.
export function TargetTag({ kind, label, notes }: { kind: 'never' | 'unavailable'; label?: ReactNode; notes: ReactNode }) {
  const { t } = useI18n()
  return <HoverCard className="target-tag-anchor" content={<div className="target-notes">{notes}</div>}>
    <em className={`target-tag ${kind}`}><AlertTriangle size={12} />{label ?? t(kind === 'never' ? 'target.never' : 'target.unavailable')}</em>
  </HoverCard>
}
