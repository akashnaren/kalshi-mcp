import crypto from "node:crypto";

/** Sign `timestamp + METHOD + path` with the query string removed. */
export function signRequest({ privateKey, timestamp, method, path }) {
  const pathWithoutQuery = String(path).split("?")[0];
  const message = Buffer.from(`${timestamp}${String(method).toUpperCase()}${pathWithoutQuery}`);
  const key = typeof privateKey === "string" ? crypto.createPrivateKey(privateKey) : privateKey;
  const signature = key.asymmetricKeyType === "ed25519"
    ? crypto.sign(null, message, key)
    : crypto.sign("sha256", message, {
      key,
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
      saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
    });
  return signature.toString("base64");
}
