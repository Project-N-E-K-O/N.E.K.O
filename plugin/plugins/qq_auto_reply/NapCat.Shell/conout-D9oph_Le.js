var ConoutWorkerMessage = /* @__PURE__ */ ((ConoutWorkerMessage2) => {
  ConoutWorkerMessage2[ConoutWorkerMessage2["READY"] = 1] = "READY";
  return ConoutWorkerMessage2;
})(ConoutWorkerMessage || {});
function getWorkerPipeName(conoutPipeName) {
  return `${conoutPipeName}-worker`;
}

export { ConoutWorkerMessage as C, getWorkerPipeName as g };
