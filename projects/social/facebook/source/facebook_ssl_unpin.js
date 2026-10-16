"use strict";

let verifyHits = 0;
let verifyHookInstalled = false;
let coldstartVerifyHits = 0;
let coldstartVerifyHookInstalled = false;

function installX509VerifyHook() {
  if (verifyHookInstalled) {
    return true;
  }
  const verify = Module.findGlobalExportByName("X509_verify_cert");
  if (verify === null) {
    return false;
  }

  Interceptor.attach(verify, {
    onLeave(retval) {
      if (retval.toInt32() !== 1) {
        retval.replace(1);
      }
      verifyHits += 1;
      if (verifyHits <= 3) {
        console.log(`[facebook-unpin] X509_verify_cert accepted hit=${verifyHits}`);
      }
    },
  });

  verifyHookInstalled = true;
  console.log(`[facebook-unpin] hook installed at ${verify}`);
  return true;
}

function installColdstartX509VerifyHook() {
  if (coldstartVerifyHookInstalled) {
    return true;
  }
  const coldstart = Process.findModuleByName("libcoldstart.so");
  if (coldstart === null) {
    return false;
  }
  const verify = coldstart.findExportByName("X509_verify_cert");
  if (verify === null) {
    return false;
  }

  Interceptor.attach(verify, {
    onLeave(retval) {
      if (retval.toInt32() !== 1) {
        retval.replace(1);
      }
      coldstartVerifyHits += 1;
      if (coldstartVerifyHits <= 3) {
        console.log(`[facebook-unpin] coldstart X509_verify_cert accepted hit=${coldstartVerifyHits}`);
      }
    },
  });

  coldstartVerifyHookInstalled = true;
  console.log(`[facebook-unpin] coldstart X509 hook installed at ${verify}`);
  return true;
}

let retryCount = 0;
const retryTimer = setInterval(() => {
  retryCount += 1;
  const globalReady = installX509VerifyHook();
  const coldstartReady = installColdstartX509VerifyHook();
  if ((globalReady && coldstartReady) || retryCount >= 60) {
    clearInterval(retryTimer);
  }
}, 250);
