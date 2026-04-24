# Understanding Thermal Comfort: PMV, PPD, and Adaptive PMV

If you are dealing with building HVAC (Heating, Ventilation, and Air Conditioning) systems, the ultimate goal is to make people feel comfortable while saving energy. But "comfort" is subjective. How do we measure a feeling?

This document explains the three core concepts used in this project to measure and predict human comfort in simple terms: **PMV**, **PPD**, and **Adaptive PMV (aPMV)**.

---

## 1. Fanger’s Standard PMV (Predicted Mean Vote)

### What is it?
Created by Professor P.O. Fanger in the 1970s, the **PMV (Predicted Mean Vote)** is the most widely used thermal comfort model in the world (standardized as ISO 7730 and ASHRAE 55). 

Instead of just looking at a thermometer on the wall, PMV calculates "how the average person will vote on their thermal sensation" on a 7-point scale:

* **+3** : Hot
* **+2** : Warm
* **+1** : Slightly Warm
* **0**  : Neutral (Perfectly comfortable)
* **-1** : Slightly Cool
* **-2** : Cool
* **-3** : Cold

### How does it work?
The PMV model recognizes that comfort isn't just about air temperature. It uses **6 exact variables** (4 from the environment, 2 from the person) to calculate a human's "heat balance" (how much heat your body makes vs. how much it loses to the room).

**Environmental Factors (Measured by Sensors):**
1. **Air Temperature ($T_a$):** The standard temperature of the room.
2. **Mean Radiant Temperature ($T_r$):** The heat radiating from solid surfaces (like a hot sun-facing window, or a cold wall).
3. **Relative Humidity (RH):** How much moisture is in the air (high humidity makes it harder for sweat to evaporate).
4. **Air Velocity ($v_a$):** How fast the air is moving (e.g., draft from a fan or AC).

**Personal Factors (Measured via Surveys):**
5. **Clothing Insulation (CLO / $I_{cl}$):** How heavily a person is dressed. (e.g., Shorts and a t-shirt = ~0.3 CLO. A thick winter coat = ~1.0+ CLO).
6. **Metabolic Rate (MET / $M_{met}$):** How much physical energy a person is burning. (e.g., Sleeping = 0.8 MET. Sitting and typing = 1.0 MET. Walking actively = 2.0+ MET).

*In our script, the `pmv_ppd()` function takes these 6 inputs and runs a complex iterative math equation to simulate human heat transfer and spit out a PMV number from -3 to +3.*

---

## 2. PPD (Predicted Percentage of Dissatisfied)

### What is it?
You can't please everyone. Even if a room is perfectly tuned to **PMV = 0** (Neutral), some people will naturally run a little bit hot, and others will run a little bit cold. 

**PPD (Predicted Percentage of Dissatisfied)** translates the PMV score into a simple percentage: **What percent of people in this room will complain that they are uncomfortable?**

### The 5% Rule
Because human biology varies, the PPD curve is U-shaped:
* If PMV is **0** (perfect), the PPD is **5%**. (This means 5% of people will still be dissatisfied no matter what you do!).
* If PMV jumps to **+1** (slightly warm) or **-1** (slightly cool), the PPD jumps to about **26%** dissatisfied.
* If PMV is extreme (like **-3** or **+3**), the PPD hits **100%**.

The general goal of good HVAC engineering is to keep the PMV between **-0.5 and +0.5**, which guarantees that **less than 10% of people will be deeply dissatisfied (PPD < 10%)**.

---

## 3. Adaptive PMV (aPMV)

### The Problem with Fanger's Standard PMV
Fanger's standard PMV was developed in climate-controlled climate chambers with European adults. It assumes humans are passive subjects in sealed, air-conditioned boxes.

In reality, humans **adapt**. If you live in a warm-humid climate (like India or Southeast Asia), a room at 28°C might feel perfectly comfortable to you. However, Fanger's PMV equation might look at 28°C and predict that everyone is sweating (PMV = +1.5). Standard PMV overestimates discomfort in naturally ventilated or mixed-mode buildings.

This adaptation takes three forms:
1. **Behavioral:** Opening a window, turning on a desk fan, taking off a jacket.
2. **Physiological:** Your body sweating more efficiently over time.
3. **Psychological:** Because you know it's hot outside, your brain expects it to be slightly warmer inside, shifting your baseline.

### What is Adaptive PMV (aPMV)?
Adaptive PMV (developed heavily by researchers like Yao et al. in 2009) fixes this by applying a real-world **calibration coefficient** called **Lambda ($\lambda$)**.

The equation is:
**aPMV = PMV / (1 + $\lambda$ * PMV)**

### How our implementation does it:
In our `data_ingestion.py` script, we don't just guess the $\lambda$ coefficient. We calculate it dynamically based on the users' real Google Form responses!

1. When a user fills out the form, they tell us their **Actual Mean Vote (AMV)** — how they *actually* feel on the -3 to +3 scale.
2. The script compares their real vote (**AMV**) to the computer's guess (**PMV**). 
3. If the math says they should be hot (+1.5) but they voted that they are neutral (0), the script calculates the discrepancy.
4. Over a rolling window of recent survey responses, the script averages out these discrepancies to calculate a custom **$\lambda$ coefficient** specific to your building's exact climate and occupants.
5. It then uses this $\lambda$ to calculate the final `apmv`, giving your dataset a highly accurate, AI-ready target metric that proves whether your occupants are actually inside the true **Comfort Zone**.