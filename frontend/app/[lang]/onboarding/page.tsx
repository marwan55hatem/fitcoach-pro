import { OnboardingWizard } from "@/components/onboarding/wizard";

// Shown only after the client-side login check, which instant-navigation checks can't see.
export const instant = false;

export default function OnboardingPage() {
  return <OnboardingWizard />;
}
