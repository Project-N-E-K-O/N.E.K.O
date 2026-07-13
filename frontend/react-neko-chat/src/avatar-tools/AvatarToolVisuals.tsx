import { createPortal } from 'react-dom';
import type { CSSProperties } from 'react';
import { getAvatarToolRegistration } from './catalog';
import type { AvatarToolTransientVisualEffect } from './feedback';
import type { AvatarToolVisualModel } from './visualState';

function AvatarToolTransientEffectVisual({ effect }: { effect: AvatarToolTransientVisualEffect }) {
  if (effect.kind === 'random-scatter-v1') {
    return (
      <span
        className="avatar-tool-random-scatter-particle"
        aria-hidden="true"
        style={{
          position: 'fixed',
          left: `${effect.x}px`,
          top: `${effect.y}px`,
          '--drop-drift-x': `${effect.driftX}px`,
          '--drop-drift-y': `${effect.driftY}px`,
          '--drop-rotation': `${effect.rotation}deg`,
          '--drop-scale': effect.scale,
          '--drop-delay': `${effect.delayMs}ms`,
          animationDuration: `${effect.recipe.lifetimeMs}ms`,
        } as CSSProperties}
      >
        <img
          className="avatar-tool-random-scatter-particle-image"
          src={effect.recipe.assetPath}
          alt=""
          style={{ animationDuration: `${effect.recipe.lifetimeMs}ms` }}
        />
      </span>
    );
  }
  return (
    <span
      className="avatar-tool-fixed-particle"
      aria-hidden="true"
      style={{
        left: `${effect.x}px`,
        top: `${effect.y}px`,
        '--heart-drift-x': `${effect.driftX}px`,
        '--heart-drift-y': `${effect.driftY}px`,
        '--heart-sway-x': `${Math.max(8, Math.round(Math.abs(effect.driftX) * 0.32)) * (effect.driftX < 0 ? -1 : 1)}px`,
        '--heart-scale': effect.scale,
        '--heart-delay': `${effect.delayMs}ms`,
        animationDuration: `${effect.recipe.lifetimeMs}ms`,
      } as CSSProperties}
    >
      <span
        className="avatar-tool-fixed-particle-glyph"
        style={{ animationDuration: `${effect.recipe.lifetimeMs}ms` }}
      >
        {effect.recipe.glyph}
      </span>
    </span>
  );
}

export default function AvatarToolVisuals({ model }: { model: AvatarToolVisualModel }) {
  const activeVisual = model.activeToolId
    ? getAvatarToolRegistration(model.activeToolId).definition.visual
    : null;
  const activeVisualMode = activeVisual
    ? (model.overlayCompact ? activeVisual.pointer : activeVisual.inRange)
    : null;
  const toolVisual = model.activeTool && model.overlayActive && !model.overlayEffect ? (
    <div
      ref={model.overlayRef}
      className={`avatar-tool-visual-overlay avatar-tool-visual-overlay-${model.activeTool.id} is-visible${model.overlayCompact ? ' is-compact' : ''}`}
      aria-hidden="true"
      style={{
        '--avatar-tool-visual-overlay-scale': activeVisualMode?.scale ?? 1,
      } as CSSProperties}
    >
      <div className="avatar-tool-visual-overlay-stage" style={{ transformOrigin: '0 0' }}>
        <img
          className={`avatar-tool-visual-overlay-image avatar-tool-visual-overlay-image-${model.activeTool.id}`}
          src={model.overlayImagePath}
          alt=""
          style={{
            width: `${activeVisualMode?.displayWidth ?? 0}px`,
            height: `${activeVisualMode?.displayHeight ?? 0}px`,
          }}
        />
      </div>
    </div>
  ) : null;

  const overlayEffect = model.overlayEffect;
  const overlayEffectDurationMs = overlayEffect?.recipe.timeline[
    overlayEffect.recipe.timeline.length - 1
  ]?.delayMs ?? 0;
  const overlayEffectEasterActive = !!overlayEffect
    && overlayEffect.mode === overlayEffect.recipe.easterEgg.mode;
  const impactEffectVisual = model.overlayActive && overlayEffect && activeVisualMode ? (
    <div
      ref={model.overlayRef}
      className={`avatar-tool-impact-effect is-visible${model.overlayCompact ? ' is-compact' : ''}${overlayEffectEasterActive ? ' is-easter-egg' : ''}`}
      aria-hidden="true"
      style={{
        '--avatar-tool-impact-effect-visual-scale': activeVisualMode.scale,
        '--avatar-tool-impact-effect-scale': overlayEffectEasterActive ? overlayEffect.recipe.easterEgg.scale : 1,
        '--avatar-tool-impact-effect-anchor-fix-x': `${overlayEffectEasterActive ? overlayEffect.recipe.easterEgg.anchorOffset.x : 0}px`,
        '--avatar-tool-impact-effect-anchor-fix-y': `${overlayEffectEasterActive ? overlayEffect.recipe.easterEgg.anchorOffset.y : 0}px`,
        '--avatar-tool-impact-origin-x': `${overlayEffect.recipe.impactRegistration.transformOrigin.x}px`,
        '--avatar-tool-impact-origin-y': `${overlayEffect.recipe.impactRegistration.transformOrigin.y}px`,
        '--avatar-tool-impact-translate-x': `${overlayEffect.recipe.impactRegistration.translate.x}px`,
        '--avatar-tool-impact-translate-y': `${overlayEffect.recipe.impactRegistration.translate.y}px`,
        '--avatar-tool-impact-rotation': `${overlayEffect.recipe.impactRegistration.rotationDeg}deg`,
        '--avatar-tool-impact-scale': overlayEffect.recipe.impactRegistration.scale,
      } as CSSProperties}
    >
      <div className="avatar-tool-impact-effect-stage" style={{ transformOrigin: '0 0' }}>
        {model.overlayCompact ? (
          <img
            className="avatar-tool-impact-effect-pointer-image"
            src={overlayEffect.pointerImagePath}
            alt=""
            style={{ width: `${activeVisualMode.displayWidth}px`, height: `${activeVisualMode.displayHeight}px` }}
          />
        ) : (
          <div
            className={`avatar-tool-impact-effect-visual${overlayEffect.phase !== 'idle' ? ' is-active' : ' is-idle'}${overlayEffect.phase === 'impact' ? ' is-impact' : ''}`}
            style={{
              width: `${activeVisualMode.displayWidth}px`,
              height: `${activeVisualMode.displayHeight}px`,
              transformOrigin: `${overlayEffect.recipe.transformOrigin.x}px ${overlayEffect.recipe.transformOrigin.y}px`,
              animationDuration: `${overlayEffectDurationMs}ms`,
            }}
          >
            <img className="avatar-tool-impact-effect-image avatar-tool-impact-effect-image-primary" src={overlayEffect.idleImagePath} alt="" />
            <img className="avatar-tool-impact-effect-image avatar-tool-impact-effect-image-secondary" src={overlayEffect.impactImagePath} alt="" />
          </div>
        )}
      </div>
    </div>
  ) : null;

  const visuals = (
    <>
      {toolVisual}
      {impactEffectVisual}
    </>
  );

  return (
    <>
      {model.transientEffects.map(effect => (
        <AvatarToolTransientEffectVisual key={effect.id} effect={effect} />
      ))}
      {typeof document !== 'undefined' ? createPortal(visuals, document.body) : visuals}
    </>
  );
}
