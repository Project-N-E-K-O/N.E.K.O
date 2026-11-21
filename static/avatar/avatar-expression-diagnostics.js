/* Opt-in, bounded, in-memory sampling immediately before Cubism computes drawables. */
(function () {
    'use strict';
    let enabled = false,
        binding = null,
        timer = null,
        samples = [];
    const ids = [
        'ParamAngleZ',
        'ParamBodyAngleZ',
        'Param86',
        'ParamEyeLOpen',
        'ParamEyeROpen',
        'ParamEyeLSmile',
        'ParamMouthForm',
        'ParamMouthOpenY',
        'ParamBrowLY',
        'ParamBrowLForm',
        'ParamBrowLAngle',
        'ParamCheek',
        'Param74',
        'Param75',
        'Param76',
        'Param77',
        'Param94',
        'Param95',
        'Param96'
    ];
    function detach() {
        if (binding && binding.native.update === binding.wrapper) binding.native.update = binding.original;
        binding = null;
    }
    function bind() {
        const manager = window.live2dManager,
            model = manager?.currentModel;
        const core = model?.internalModel?.coreModel,
            native = core?._model;
        if (binding?.native === native) return;
        detach();
        if (!native?.update) return;
        const original = native.update;
        let last = 0;
        const wrapper = function () {
            try {
                if (enabled && manager.currentModel === model && performance.now() - last > 450) {
                    last = performance.now();
                    const p = native.parameters;
                    samples.push({
                        at: Date.now(),
                        model: manager.modelName,
                        ...window.nekoMusicListening?.snapshot?.(),
                        performance: manager._manualExpressionResource?.performance?.snapshot?.() || null,
                        parameters: Object.fromEntries(
                            ids
                                .filter((id) => p.ids.includes(id))
                                .map((id) => {
                                    const i = p.ids.indexOf(id);
                                    return [
                                        id,
                                        {
                                            value: p.values[i],
                                            min: p.minimumValues[i],
                                            max: p.maximumValues[i]
                                        }
                                    ];
                                })
                        )
                    });
                    if (samples.length > 120) samples.shift();
                }
            } catch (_) {
                /* Diagnostics must never interrupt rendering. */
            }
            return original.apply(this, arguments);
        };
        binding = { native, original, wrapper };
        native.update = wrapper;
    }
    window.nekoExpressionDiagnostics = {
        enable(value = true) {
            enabled = !!value;
            if (timer) window.clearInterval(timer);
            timer = null;
            if (enabled) {
                bind();
                timer = window.setInterval(bind, 500);
            } else {
                detach();
                samples = [];
            }
        },
        snapshot: () => ({
            enabled,
            state: window.nekoMusicListening?.snapshot?.(),
            samples: samples.slice()
        })
    };
    window.addEventListener('pagehide', () => window.nekoExpressionDiagnostics.enable(false));
})();
