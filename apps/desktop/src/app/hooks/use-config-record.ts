import { useQuery } from '@tanstack/react-query'

import { getHermesConfigRecord, getHermesConfigRecordForProfile } from '@/hermes'
import { queryClient } from '@/lib/query-client'
import type { HermesConfigRecord } from '@/types/hermes'

// One shared cache for the whole profile config record (`GET /api/config`).
// Every settings surface (MCP, model, config) reads and writes through this key
// so a save in one shows in the others, and revisiting a tab paints the cache
// instead of blanking on a fresh fetch.
//
// Distinct from session/hooks/use-hermes-config.ts, which is side-effecting —
// it pushes personality/cwd/voice/… into the session stores for live chat.
export const HERMES_CONFIG_KEY = ['hermes-config-record'] as const
const hermesConfigKey = (profile?: null | string) => [...HERMES_CONFIG_KEY, profile || 'default'] as const

// staleTime 0 → serve cache instantly, background-revalidate on every mount.
export const useHermesConfigRecord = (profile?: null | string) =>
  useQuery({
    queryKey: hermesConfigKey(profile),
    queryFn: () => (profile ? getHermesConfigRecordForProfile(profile) : getHermesConfigRecord()),
    staleTime: 0
  })

export const setHermesConfigCache = (config: HermesConfigRecord, profile?: null | string) =>
  queryClient.setQueryData<HermesConfigRecord>(hermesConfigKey(profile), config)

export const invalidateHermesConfig = () => queryClient.invalidateQueries({ queryKey: HERMES_CONFIG_KEY })
