export function Spinner({ size = 20 }: { size?: number }) {
  return (
    <div
      className="spinner"
      style={{ width: size, height: size }}
    />
  )
}

export function LoadingCenter() {
  return (
    <div className="loading-center">
      <Spinner size={28} />
    </div>
  )
}
