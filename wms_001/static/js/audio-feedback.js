(function () {
    const AudioFeedback = {
        audioReady: false,
        initialized: false,
        activeAudio: null,
        ok: null,
        error: null,
        done: null,
    };

    function stopActive() {
        if (!AudioFeedback.activeAudio) {
            return;
        }
        try {
            AudioFeedback.activeAudio.pause();
            AudioFeedback.activeAudio.currentTime = 0;
        } catch (_) {}
        AudioFeedback.activeAudio = null;
    }

    function resetAndPlay(audio) {
        if (!audio) {
            return;
        }
        stopActive();
        try {
            audio.currentTime = 0;
            const playPromise = audio.play();
            AudioFeedback.activeAudio = audio;
            if (playPromise && typeof playPromise.then === 'function') {
                playPromise.catch(function () {});
            }
        } catch (_) {}
    }

    function unlockAudio() {
        if (AudioFeedback.audioReady || !AudioFeedback.ok) {
            return;
        }
        try {
            AudioFeedback.ok.currentTime = 0;
            const playPromise = AudioFeedback.ok.play();
            if (playPromise && typeof playPromise.then === 'function') {
                playPromise
                    .then(function () {
                        AudioFeedback.ok.pause();
                        AudioFeedback.ok.currentTime = 0;
                        AudioFeedback.audioReady = true;
                    })
                    .catch(function () {});
            }
        } catch (_) {}
    }

    function init() {
        if (AudioFeedback.initialized) {
            return;
        }
        AudioFeedback.ok = new Audio('/static/sounds/success.wav');
        AudioFeedback.error = new Audio('/static/sounds/error.wav');
        AudioFeedback.done = new Audio('/static/sounds/done.wav');
        AudioFeedback.ok.volume = 1.0;
        AudioFeedback.error.volume = 1.0;
        AudioFeedback.done.volume = 1.0;
        document.addEventListener('click', unlockAudio, { passive: true });
        document.addEventListener('keydown', unlockAudio);
        AudioFeedback.initialized = true;
    }

    window.WMSAudioFeedback = {
        init: init,
        unlock: unlockAudio,
        beepOk: function () {
            resetAndPlay(AudioFeedback.ok);
        },
        beepErro: function () {
            resetAndPlay(AudioFeedback.error);
        },
        beepFinalizado: function () {
            resetAndPlay(AudioFeedback.done);
        },
    };
})();
