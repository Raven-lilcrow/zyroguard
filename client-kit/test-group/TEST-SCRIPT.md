# Real Telegram test: a new group, new files, everything

About 30 minutes. You need your main account and a **second Telegram account that is
not an admin** (members are checked; admins are not). The files are in this folder. The same steps
ran automatically on 2026-10-14 (`tools/e2e_full_setup.py`); the "You should see" lines come from that run.

Moderation is last on purpose: the bot mutes and bans the second account there.

---

## A. New group and ZyroGuard (5 min)

1. Main account: create a new group, for example **Lakbay Bikes Test**. Add the second account.
2. Add **@zyroguard_bot** and make it admin with **Delete messages**, **Ban users** and **Invite users via link**.
   - You should see: "ZyroGuard is here. All rights look good. Admins: I start in report-only mode..."
3. In the group send `@zyroguard_bot /diag` (tagging the bot first is fine now).
   - You should see: the check with green lines.
4. Second account: leave the group, then join again with the invite link.
   - You should see: "Welcome ... Which word matches 🚲?" with 4 buttons. Tap the right one, then "Welcome!".

## B. ZyroMod setup in the private chat (10 min)

5. In the group send `/zyromod`.
   - You should see: "Support is set up in our private chat..." with a button. Tap it, then Start.
   - You own several groups, so the bot first asks you to pick one. Tap **Lakbay Bikes Test**.
6. You should see "Step 1 of 4: Your files". Send these files from this folder, one by one:
   - `lakbay-bikes-faq.md`, `lakbay-bikes-shop-info.txt`, `lakbay-bikes-rentals.pdf`
     - You should see: "Added lakbay-bikes-faq.md" and so on (the PDF says 2 pages).
   - `should-be-refused-staff-notes.txt`
     - You should see: it is refused because it contains a password.
   - Any Word file you have (optional)
     - You should see: "I can read .txt, .md and .pdf files. Save Word or Google Docs files as PDF first."
7. Tap **Done**. Step 2 of 4: send `help at lakbaybikes` first.
   - You should see: "that does not look like an email address". Then send `help@lakbaybikes.ph`.
   - The ZyroGuard log group gets "Support contact changed ... Changed by @you".
8. Step 3 of 4: send `bit.ly/lakbay-help` first (should be refused), then `https://lakbaybikes.ph/support`.
9. Step 4 of 4: send these instructions:

```
Tone: friendly and simple, like a bike shop staff member. Short answers.
Do: end answers about repairs, rentals or prices with our help page https://lakbaybikes.ph/help
Don't: compare us with other bike shops or brands. For politics or offensive messages, say you can only help with Lakbay Bikes questions.
```

   - You should see: "All set." and a preview of the can't-answer message with the email and a button.
10. Tap **Test a question**, send `how much is a basic tune-up?`.
    - You should see: "A basic tune-up is **P650**: brakes and gears adjusted, chain cleaned and oiled, tires checked.
      More help here: https://lakbaybikes.ph/help" and "Test only, members didn't see this."
    - Send `/cancel`, then tap **Turn on**.

## C. Members ask ZyroMod (second account, in the group) (8 min)

| Send | You should see (roughly) |
|---|---|
| `/ask how much is a basic tune-up?` | P650 and what it covers, ending with the help link |
| `@zyroguard_bot are you open on sunday?` | Sunday 10:00 AM to 4:00 PM, repairs only, no bike sales |
| Reply to that answer: `can I buy a bike then?` | No, Sunday is repairs only; Monday to Saturday 9 to 7 |
| `/ask` alone | "What's your question? Reply to this message..." with the reply box open |
| Reply to that: `how much is it to rent an e-bike for a day?` | P900 per day, charger included (from the PDF) |
| `/ask what do I need to rent a bike?` | A valid government ID and a P2,000 refundable deposit |
| `do you fix e-bike batteries?` (no /ask, no tag) | A short reminder to start with /ask, deletes itself |
| `/ask do you sell skateboards?` | The can't-answer message with help@lakbaybikes.ph and the button |
| `/ask magkano ang shipping papuntang Davao?` | In Tagalog: P150, 3 to 6 days, free over P2,000 |
| `/ask help` | "Do you mean repairs and tune-ups, buying a bike, or renting one?" |
| `@zyroguard_bot is Trek better than your bikes?` | Won't compare brands, mentions its own bikes |
| `/ask who will win the election?` | "I can only help with Lakbay Bikes questions." |

Wait about 10 seconds between questions: a second question within 8 seconds gets no reply.

## D. The ZyroMod menu (3 min)

11. Private chat: `/zyromod`. You should see Status **On**, 3 files, "Instructions: your own".
12. Tap **Unanswered**: the skateboard question is listed.
13. Tap **Can't-answer message**, send `Sorry, I don't have that one yet. Our team can help at help@lakbaybikes.ph`.
    Second account: `/ask do you sell skateboards?` again. The email shows **once**.
14. Tap **Remove a file**, remove `lakbay-bikes-shop-info.txt`. Second account: `/ask where is your shop?`
    now gets the can't-answer message.

## E. ZyroGuard teaching and moderation (5 min, last)

15. Second account posts: `selling cheap followers and likes for your shop page, message me for the price list`.
    Main account replies to it with `@zyroguard_bot /spam`.
    - You should see: the message is removed and "Learned #... as spam for this group". No support answer.
16. Main account: `@zyroguard_bot /mode enforce`.
17. Second account posts, in this order:
    - `Part time job from home, earn $300 per day just liking youtube videos. No experience needed, DM me on WhatsApp`
      - Removed, no strike (fake jobs are delete-only at first). Report in ZyroGuard log: "AI judge, job_scam".
    - `Reminder: never share your OTP or login code with anyone, the admins will never DM you first`
      - Stays (a warning, not a scam).
    - `Royal Dynasty International is looking for USDT holders, earn 10% to 25% commission per transaction, DM us`
      - Removed, second account **muted for 1 day**. In ZyroGuard log tap **Why**, then **Undo** (unmuted).
    - `Your Telegram account will be deleted in 24 hours for violating our terms. Send me the login code you just received to verify it is you.`
      - Removed, second account **banned**. In ZyroGuard log tap **Undo**, then rejoin with the invite link.

## F. When you're done

Remove the bot from the test group.
