/** A device's photo, or the drawn placeholder the server returns when no site has one. */
export default function DeviceImage({ productKey, name, size = "md" }: {
  productKey: string;
  name: string;
  size?: "sm" | "md" | "lg";
}) {
  return (
    <img className={`device-img ${size}`} src={`/api/images/${encodeURIComponent(productKey)}`}
         alt={name} loading="lazy" decoding="async" />
  );
}
