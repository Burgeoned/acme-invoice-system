# Acme Invoice Review Tool — AP Team Guide

This guide is for anyone using the Invoice Review tool to process vendor invoices. No technical background needed.

---

## What this tool does

Vendors send us invoices to get paid. Before we pay, we need to make sure the invoice is legitimate, the amounts are correct, and the vendor is someone we actually work with.

This tool reads every invoice that comes in, checks it automatically, and either:
- **Approves it** and sends payment — no action needed from you
- **Rejects it** automatically — obvious problems like fraud flags or bad data
- **Sends it to your queue** — needs a human decision before anything happens

Your job is the third category. The tool surfaces the ones that need your attention and gives you everything you need to make the call.

---

## Getting started

When you open the tool, you'll see two tabs at the top: **Batch Processing** and **Single Invoice**.

**Batch Processing** is what you'll use day to day. Click **Run Batch** to process all the invoices in the inbox at once. Results appear within seconds.

**Single Invoice** is for one-off uploads — drag in a file, click Run Invoice, see the result immediately.

---

## The main screen after running a batch

The screen is organized so the most important thing is at the top.

**Needs Your Attention** — these are the invoices waiting for your decision. This is your to-do list. Everything here is paused until you act on it.

**Summary** — counts of what happened: how many were approved automatically, how many were rejected, how many need your review, and the total dollar value that went through without anyone needing to touch it.

**Already Handled** — everything that's been resolved, collapsed by default. Open it if you want to audit what went through or look something up.

---

## Making a decision on an invoice

Each card in the "Needs Your Attention" section shows you:
- **Vendor name** and invoice number
- **Total amount**
- **Why it's here** — a plain-English description of the issue

Click **Details** to see the full picture: extracted fields, flags, the AI's reasoning, and the original invoice document side by side.

**To approve:** click the green Approve button. Payment will process automatically and the invoice moves to Already Handled.

**To reject:** click the red Reject button, type a reason (required), then click Confirm Rejection. The reason gets logged with the invoice.

**To accept a revision:** if an invoice is flagged as a possible revision of one we already processed, and the original wasn't paid yet, an "Accept Revision" button appears. This processes the updated version.

---

## What the flags mean

When an invoice gets flagged, here's what each message means in plain terms:

| Flag | What it means | What to do |
|------|---------------|------------|
| Vendor not on approved list | We haven't worked with this vendor before | Check with procurement before approving |
| Vendor name closely matches a known vendor | The name is almost but not exactly right — could be a typo or could be someone trying to impersonate a vendor | Compare carefully with the invoice document |
| Vendor is flagged as a bad actor | This vendor is on our blocked list | Reject |
| Quantity exceeds authorized limit | They're invoicing for more than we have on order | Check against the purchase order |
| Item not available for ordering | This item isn't currently in our catalog | Check with procurement |
| Item not in catalog | We've never ordered this item before | Verify it was actually ordered |
| Price deviates from expected | The price is more than 15% off what we normally pay | Check for a rush order surcharge or pricing error |
| Invoice is not in USD | We received a foreign currency invoice | Needs finance sign-off on the exchange rate before we can pay |
| Invoice number already processed | We've already paid or processed this invoice number | Confirm it's not a duplicate billing |
| Possible revised invoice | Same invoice number as one we already have, but the amounts are different | Compare both versions — may be a legitimate amendment |
| Payment already sent for original | The original version was already paid | Do not approve here. Contact finance for a credit memo or supplemental invoice. |
| Negative quantity or total | The numbers don't make sense — data error | Reject and ask vendor to resubmit |
| Extraction confidence was low | The system had trouble reading this invoice clearly | Review the original document carefully before deciding |

---

## The AI reasoning section

When you open an invoice's details, you'll see an **AI reasoning** block. This is the system's explanation for why it made the decision it did — or why it flagged something for your review.

Read it, but use your judgment. The AI is good at pattern-matching and applying our policies, but you're the one who knows the business context. If the AI escalated something that you know is fine, you can approve it. That's why the button is there.

---

## Already Handled — auditing past decisions

Open the "Already Handled" section to see every invoice that's been resolved in this session, sorted by invoice number. Click **Details** on any row to see the full record: what was extracted, what flags were raised, what the AI decided, and whether payment went through.

If you need to reverse a rejection, click **Override** on the rejected invoice. You'll see a confirmation message, then "Approve and process payment." This is intentionally a two-step process — overriding a rejection is a deliberate action and gets logged.

---

## Resetting for a new session

**Reset view** clears the screen so you can run a fresh batch. All decisions stay saved in the database — this just clears what you're looking at.

**Reset DB** is for testing only. It wipes all records. You will be asked to confirm before anything is deleted. Don't use this on real invoice data.

---

## Things to know

**The tool processes PDFs, spreadsheets, JSON, XML, and plain text.** If a vendor sends an invoice in an unusual format, it should still work. If it doesn't, the system will flag it as low confidence and route it to your queue.

**If the same invoice comes in twice,** the system catches it and routes the second one to your queue with a note about the original. This prevents duplicate payments.

**If a vendor sends a revised invoice** with the same number but different amounts, the system flags it as a possible revision so you can compare both versions before deciding.

**Approved invoices are logged automatically.** You don't need to do anything after clicking Approve. The payment confirmation and full invoice record are saved.

**Questions or something not working?** Reach out to the team that set this up. The logs folder has a full record of every invoice that's been processed if someone needs to investigate.
