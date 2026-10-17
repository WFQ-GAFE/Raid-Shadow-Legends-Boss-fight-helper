import { createContext, useContext, useEffect, useRef, useState } from 'react'
import { championId } from './heroData'

type PortraitHero = { typeId: number; name?: string }
const portraitRevision = createContext('')
export const HeroAvatarRevisionProvider = portraitRevision.Provider

// Roster data can arrive before the display catalog on a fresh installation.
// Portraits are local assets keyed by the champion's stable base id.
export function heroAvatarIdentity(hero?: PortraitHero, typeId?: number) {
  const candidate = hero?.typeId ?? typeId
  if (typeof candidate !== 'number' || !Number.isSafeInteger(candidate) || candidate <= 0) return undefined
  const identity = championId(candidate)
  return identity > 0 ? identity : undefined
}

export function HeroAvatar({ hero, typeId, name, revision, size = 'md' }: {
  hero?: PortraitHero
  typeId?: number
  name?: string
  revision?: string
  size?: 'sm' | 'md' | 'lg'
}) {
  const sharedRevision = useContext(portraitRevision)
  const assetRevision = revision ?? sharedRevision
  const identity = heroAvatarIdentity(hero, typeId)
  const [sourceIndex, setSourceIndex] = useState(0)
  const [requestRevision, setRequestRevision] = useState(assetRevision)
  const requestedIdentity = useRef(identity)
  // A failed request is retried only when the catalog/assets become ready, or
  // the champion changes. Normal state polling must not repeatedly fetch it.
  useEffect(() => {
    if (requestedIdentity.current !== identity || (sourceIndex > 0 && requestRevision !== assetRevision)) {
      requestedIdentity.current = identity
      setSourceIndex(0)
      setRequestRevision(assetRevision)
    }
  }, [identity, assetRevision, sourceIndex, requestRevision])
  // Successful portraits keep their URL when unrelated catalog data changes.
  const version = requestRevision ? `?revision=${encodeURIComponent(requestRevision)}` : ''
  const sources = identity === undefined ? [] : [
    `/api/asset/hero/${identity}${version}`,
    `/hero-fallbacks/${identity}.png${version}`,
  ]
  return (
    <span className={`avatar avatar-${size}`} aria-hidden="true">
      {identity !== undefined && sourceIndex < sources.length ? (
        <img src={sources[sourceIndex]} alt="" loading="lazy" decoding="async"
          onError={() => setSourceIndex((current) => current === sourceIndex ? current + 1 : current)} />
      ) : (
        <span>{(hero?.name || name)?.slice(0, 1) || '?'}</span>
      )}
    </span>
  )
}
