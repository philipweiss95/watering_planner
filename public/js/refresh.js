const WEATHER_STATUS_FIELDS = [
  "last_successful_fetch_at",
  "last_attempt_at",
  "last_error",
  "data_age_minutes",
  "cache_minutes",
  "stale_after_minutes",
  "stale",
  "cache_hit",
  "cache_fallback",
  "source",
  "mode",
];

function applyWeatherStatus(target, source) {
  if (!source || typeof source !== "object") return;
  for (const field of WEATHER_STATUS_FIELDS) {
    if (source[field] !== undefined) target[field] = source[field];
  }
  if (source.fetched_at) {
    target.last_successful_fetch_at = source.fetched_at;
  }
  if (source.weather_error) {
    target.last_error = source.weather_error;
  }
}

export function mergeWeatherStatus(...sources) {
  const result = {};
  for (const source of sources) applyWeatherStatus(result, source);
  return result;
}

export function refreshIssueMessage(result) {
  return result?.evaluationError
    ? "Der Tagesplan konnte nicht aktualisiert werden."
    : "";
}

function refreshRank(options) {
  if (options.forceWeather) return 2;
  return options.weather === false ? 0 : 1;
}

function normalizeOptions(options = {}) {
  return {
    weather: options.weather !== false,
    forceWeather: Boolean(options.forceWeather),
    afterMutation: Boolean(options.afterMutation),
  };
}

function mergeOptions(current, incoming) {
  if (!current) return incoming;
  const preferred = refreshRank(incoming) > refreshRank(current)
    ? incoming
    : current;
  return {
    ...preferred,
    afterMutation: current.afterMutation || incoming.afterMutation,
  };
}

export function createRefreshCoordinator(task) {
  let running = false;
  let activeRank = -1;
  let activePromise = null;
  let pendingOptions = null;
  let pendingWaiters = [];

  async function drain() {
    running = true;
    while (pendingOptions) {
      const options = pendingOptions;
      const waiters = pendingWaiters;
      pendingOptions = null;
      pendingWaiters = [];
      activeRank = refreshRank(options);
      activePromise = Promise.resolve().then(() => task(options));
      try {
        const result = await activePromise;
        for (const waiter of waiters) waiter.resolve(result);
      } catch (error) {
        for (const waiter of waiters) waiter.reject(error);
      } finally {
        activePromise = null;
        activeRank = -1;
      }
    }
    running = false;
  }

  return function requestRefresh(options = {}) {
    const normalized = normalizeOptions(options);
    if (
      running
      && activePromise
      && !normalized.afterMutation
      && activeRank >= refreshRank(normalized)
    ) {
      return activePromise;
    }
    pendingOptions = mergeOptions(pendingOptions, normalized);
    const waiting = new Promise((resolve, reject) => {
      pendingWaiters.push({ resolve, reject });
    });
    if (!running) void drain();
    return waiting;
  };
}

async function optionalGet(api, path, fallback) {
  try {
    return { value: await api.get(path), failed: false };
  } catch (error) {
    return {
      value: {
        ...(fallback && typeof fallback === "object" ? fallback : {}),
        error: error.message,
      },
      failed: true,
    };
  }
}

export async function loadRefreshSnapshot(
  api,
  previous = {},
  options = {},
) {
  const normalized = normalizeOptions(options);
  let forcedWeather = null;
  let forcedEvaluation = null;
  let forceError = null;
  if (normalized.forceWeather) {
    try {
      const forcedResult = await api.get(
        "/api/weather?force=true&evaluate=true&slot=morning",
      );
      forcedWeather = forcedResult?.weather || forcedResult;
      forcedEvaluation = forcedResult?.evaluation || null;
    } catch (error) {
      forceError = error;
    }
  }

  const state = await api.get("/api/state");
  if (typeof options.onState === "function") {
    options.onState(state, {
      evaluationPending: normalized.weather && !forceError,
    });
  }
  const eventsPromise = optionalGet(
    api,
    "/api/watering-events?limit=50",
    { events: previous.events || [] },
  );
  const updaterPromise = optionalGet(
    api,
    "/api/update/status",
    previous.updater,
  );

  let evaluation = previous.evaluation || null;
  let evaluationError = null;
  if (normalized.weather && !forceError) {
    if (forcedEvaluation) {
      evaluation = forcedEvaluation;
    } else {
      try {
        evaluation = await api.get(
          "/api/homekit/check?auto=true&slot=morning",
        );
      } catch (error) {
        evaluationError = error;
        evaluation = null;
      }
    }
  }

  // Fetch diagnostics after weather evaluation so its attempt/error metadata
  // belongs to the same refresh instead of the preceding one.
  const diagnosticsResult = await optionalGet(
    api,
    "/api/diagnostics/notifications",
    previous.notificationDiagnostics,
  );
  const [eventsResult, updaterResult] = await Promise.all([
    eventsPromise,
    updaterPromise,
  ]);
  const diagnostics = diagnosticsResult.failed
    ? { ...diagnosticsResult.value, weather: null }
    : diagnosticsResult.value;
  const weatherStatus = mergeWeatherStatus(
    state.weather_status,
    evaluation?.weather,
    forcedWeather,
    diagnosticsResult.failed ? null : diagnostics?.weather,
    forceError ? { last_error: forceError.message } : null,
  );
  const mergedState = { ...state, weather_status: weatherStatus };
  const refreshError = forceError || evaluationError;

  return {
    patch: {
      state: mergedState,
      evaluation,
      events: eventsResult.value.events || previous.events || [],
      notificationDiagnostics: diagnostics,
      updater: updaterResult.value,
      loading: false,
      error: refreshError?.message || "",
      simulation: Boolean(evaluation?.weather?.simulation),
    },
    weatherStatus,
    forceError,
    evaluationError,
  };
}
