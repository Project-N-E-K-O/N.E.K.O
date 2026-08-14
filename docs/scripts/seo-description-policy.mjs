const RECOMMENDED_MIN_LENGTH = 40
const RECOMMENDED_MAX_LENGTH = 180

export function descriptionLengthWarning(description) {
  const length = Array.from(description).length
  if (length >= RECOMMENDED_MIN_LENGTH && length <= RECOMMENDED_MAX_LENGTH) {
    return null
  }

  return (
    `meta description length is outside the recommended ` +
    `${RECOMMENDED_MIN_LENGTH}-${RECOMMENDED_MAX_LENGTH} character range, ` +
    `found ${length}`
  )
}
