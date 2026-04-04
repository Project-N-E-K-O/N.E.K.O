import { workerData, parentPort } from 'worker_threads';
import { Socket, createServer } from 'net';
import { g as getWorkerPipeName, C as ConoutWorkerMessage } from '../conout-D9oph_Le.js';

const conoutPipeName = workerData.conoutPipeName;
const conoutSocket = new Socket();
conoutSocket.setEncoding("utf8");
conoutSocket.connect(conoutPipeName, () => {
  const server = createServer((workerSocket) => {
    conoutSocket.pipe(workerSocket);
  });
  server.listen(getWorkerPipeName(conoutPipeName));
  if (!parentPort) {
    throw new Error("worker_threads parentPort is null");
  }
  parentPort.postMessage(ConoutWorkerMessage.READY);
});
