/**
 * Narrow an arbitrary string-or-nullish ``channel`` value coming from the
 * backend into the closed union ``'stable' | 'beta' | 'unknown'``.
 *
 * Why this helper exists
 * ----------------------
 * ``PluginInstallSourceDetailMarket.channel`` (see ``types/api.ts``) is
 * declared as the literal union ``'stable' | 'beta' | 'unknown'`` so any
 * UI consumer that branches on channel (e.g. SourceDetailRow's label
 * lookup) gets compile-time exhaustiveness. The wire format is broader
 * — Python produces ``Literal["stable", "beta"]`` today but historical
 * lock files / future Market versions may carry other values
 * (``"alpha"``, ``"nightly"``, ...). Rather than letting the open string
 * leak across the type boundary we map everything that is not one of
 * the known values to ``"unknown"``, which already has UI rendering
 * (channelLabels has no ``unknown`` key, so SourceDetailRow falls
 * through to ``return ch`` and prints the raw value in italic style).
 *
 * Inputs handled
 * --------------
 * * ``'stable'`` / ``'beta'`` — pass through unchanged.
 * * ``null`` / ``undefined`` / ``''`` — collapsed to ``'unknown'``.
 * * Any other string (including ``'STABLE'``, ``'Beta'``,
 *   ``'nightly'``, etc.) — collapsed to ``'unknown'``. We do **not**
 *   case-fold the input: the backend canonical form is lowercase, so a
 *   different casing is already a "drift" we should not silently
 *   accept.
 */
export type MarketChannel = 'stable' | 'beta' | 'unknown'

export function narrowMarketChannel(
  raw: string | null | undefined,
): MarketChannel {
  return raw === 'stable' || raw === 'beta' ? raw : 'unknown'
}
