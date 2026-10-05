// Create: clone a voice from uploaded samples (with a consent checkbox), or design one from a description.
import { Button, Checkbox, Codicon, host, Input, Textarea, useQueryClient, useValue } from '@hermes/plugin-sdk'
import { type ChangeEvent, useRef, useState } from 'react'

import { type AgentPin, agentKey, type Candidate, currentAgentEpoch, currentPin, errorText, pluginCtx, post, refreshAvailability, samePin } from './api'
import { $playing, play, stop } from './audio'
import { S, useAccountText } from './strings'
import { BilledNote, card, muted } from './ui'
import { AgentChanged, cloneVoice, MAX_FILE_BYTES, MAX_FILES } from './upload'

const label = { display: 'grid', fontSize: 12, gap: 6 } as const

export function CreateTab({ pin }: { pin: AgentPin }) {
  return (
    <div style={{ display: 'grid', gap: 16, gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', padding: '0 24px' }}>
      <DesignCard pin={pin} />
      <CloneCard pin={pin} />
    </div>
  )
}

function CloneCard({ pin }: { pin: AgentPin }) {
  const billedNote = useAccountText(S.cloneBilled, S.operatorCloneBilled)
  const client = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [consent, setConsent] = useState(false)
  const [status, setStatus] = useState<null | string>(null)
  const [busy, setBusy] = useState(false)

  const pick = (event: ChangeEvent<HTMLInputElement>) => {
    const chosen = Array.from(event.target.files ?? [])
    event.target.value = ''
    const tooBig = chosen.find(file => file.size > MAX_FILE_BYTES)
    if (chosen.length > MAX_FILES) return setStatus(S.tooMany)
    if (tooBig) return setStatus(S.tooLarge(tooBig.name))
    setStatus(null)
    setFiles(chosen)
  }

  const inFlight = useRef(false)
  const submit = async () => {
    if (inFlight.current) return  // a second click in the same tick, before `busy` re-renders
    if (!samePin(currentPin(), pin)) return setStatus(S.agentChangedNothingSent)
    inFlight.current = true
    setBusy(true)
    try {
      const voice = await cloneVoice(
        files,
        { consent, description: description.trim(), title: title.trim() },
        pin,
        { current: currentPin, epoch: currentAgentEpoch, rest: (path, opts) => pluginCtx().rest(path, opts) },
        (n, sent, total) => setStatus(S.uploading(n, files.length, Math.round((sent / Math.max(1, total)) * 100)))
      )
      if (!samePin(currentPin(), pin)) return
      setStatus(S.cloned(voice.title))
      setFiles([])
      setTitle('')
      setDescription('')
      setConsent(false)
      void client.invalidateQueries({ queryKey: ['fish-audio', agentKey(pin), 'mine'] })
      void refreshAvailability()
    } catch (error) {
      if (!samePin(currentPin(), pin)) return
      setStatus(error instanceof AgentChanged ? S.agentChanged : errorText(error))
    } finally {
      inFlight.current = false
      if (samePin(currentPin(), pin)) setBusy(false)
    }
  }

  const ready = files.length > 0 && title.trim().length > 0 && consent && !busy
  return (
    <section style={card}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>{S.cloneTitle}</h2>
      <p style={{ ...muted, fontSize: 12, lineHeight: 1.5, margin: '0 0 12px' }}>{S.cloneBody}</p>
      <div style={{ display: 'grid', gap: 10 }}>
        <input accept="audio/*,.mp3,.wav,.ogg,.webm,.flac,.m4a,.mp4" hidden multiple onChange={pick} ref={input} type="file" />
        <div style={{ alignItems: 'center', display: 'flex', flexWrap: 'wrap', gap: 8 }}>
          <Button disabled={busy} onClick={() => input.current?.click()} size="xs" variant="secondary">
            <Codicon name="cloud-upload" />
            {S.chooseFiles}
          </Button>
          <span style={{ ...muted, fontSize: 12 }}>{files.map(file => file.name).join(', ')}</span>
        </div>
        <label style={label}>
          {S.voiceTitle}
          <Input disabled={busy} onChange={(e: ChangeEvent<HTMLInputElement>) => setTitle(e.target.value)} value={title} />
        </label>
        <label style={label}>
          {S.descriptionOptional}
          <Input disabled={busy} onChange={(e: ChangeEvent<HTMLInputElement>) => setDescription(e.target.value)} value={description} />
        </label>
        <label style={{ alignItems: 'flex-start', display: 'flex', fontSize: 12, gap: 8, lineHeight: 1.4 }}>
          <Checkbox
            aria-label={S.consent}
            checked={consent}
            disabled={busy}
            onCheckedChange={(value: boolean) => setConsent(value === true)}
            // The kit's unchecked border is near-white in the light theme; give it the muted text colour.
            style={consent ? undefined : { borderColor: 'var(--ui-text-tertiary)' }}
          />
          <span>{S.consent}</span>
        </label>
        <BilledNote text={billedNote} />
        <div style={{ alignItems: 'center', display: 'flex', gap: 10 }}>
          <Button disabled={!ready} loading={busy} onClick={() => void submit()}>
            {S.clone}
          </Button>
          {status && (
            <span role="status" style={{ ...muted, fontSize: 12 }}>
              {status}
            </span>
          )}
        </div>
      </div>
    </section>
  )
}

function DesignCard({ pin }: { pin: AgentPin }) {
  const billedNote = useAccountText(S.designBilled, S.operatorDesignBilled)
  const client = useQueryClient()
  const playing = useValue($playing)
  const [instruction, setInstruction] = useState('')
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [names, setNames] = useState<Record<string, string>>({})
  const [saved, setSaved] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState<null | string>(null)
  const inFlight = useRef(false)

  const design = async () => {
    if (inFlight.current) return
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: S.agentChangedNothingSent })
    inFlight.current = true
    setBusy('design')
    stop()
    try {
      const res = await post<{ candidates: Candidate[] }>('/design', { instruction: instruction.trim(), n: 2 }, 180_000)
      if (!samePin(currentPin(), pin)) return
      setCandidates(res.candidates)
      setNames({})
      setSaved({})
      void refreshAvailability()
    } catch (error) {
      if (samePin(currentPin(), pin)) host.notify({ kind: 'error', message: errorText(error) })
    } finally {
      inFlight.current = false
      if (samePin(currentPin(), pin)) setBusy(null)
    }
  }

  const save = async (candidate: Candidate) => {
    if (inFlight.current) return
    if (!samePin(currentPin(), pin)) return host.notify({ kind: 'error', message: S.agentChangedNothingSent })
    const title = (names[candidate.design_token] ?? '').trim()
    inFlight.current = true
    setBusy(candidate.design_token)
    try {
      const res = await post<{ voice: { id: string; title: string } }>('/design/save', { design_token: candidate.design_token, title }, 120_000)
      if (!samePin(currentPin(), pin)) return
      setSaved({ ...saved, [candidate.design_token]: res.voice.title })
      host.notify({ kind: 'success', message: S.saved(res.voice.title) })
      void client.invalidateQueries({ queryKey: ['fish-audio', agentKey(pin), 'mine'] })
    } catch (error) {
      if (samePin(currentPin(), pin)) host.notify({ kind: 'error', message: errorText(error) })
    } finally {
      inFlight.current = false
      if (samePin(currentPin(), pin)) setBusy(null)
    }
  }

  return (
    <section style={card}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: '0 0 4px' }}>{S.designTitle}</h2>
      <p style={{ ...muted, fontSize: 12, lineHeight: 1.5, margin: '0 0 12px' }}>{S.designBody}</p>
      <div style={{ display: 'grid', gap: 10 }}>
        <Textarea
          aria-label={S.designTitle}
          disabled={busy !== null}
          maxLength={500}
          onChange={(e: ChangeEvent<HTMLTextAreaElement>) => setInstruction(e.target.value)}
          placeholder={S.designPlaceholder}
          rows={3}
          value={instruction}
        />
        <BilledNote text={billedNote} />
        <div>
          <Button disabled={!instruction.trim() || busy !== null} loading={busy === 'design'} onClick={() => void design()}>
            {busy === 'design' ? S.designing : S.design}
          </Button>
        </div>
        {candidates.map((candidate, i) => {
          const key = `design:${candidate.design_token}`
          return (
            <div
              key={candidate.design_token}
              style={{ alignItems: 'center', borderTop: '1px solid var(--ui-stroke-tertiary)', display: 'flex', flexWrap: 'wrap', gap: 8, paddingTop: 10 }}
            >
              <Button
                disabled={!candidate.audio}
                onClick={() => samePin(currentPin(), pin) && (playing === key ? stop() : candidate.audio && play(key, candidate.audio, candidate.mime))}
                size="xs"
                variant="secondary"
              >
                <Codicon name={playing === key ? 'debug-stop' : 'play'} />
                {S.candidate(i + 1)}
              </Button>
              {saved[candidate.design_token] ? (
                <span style={{ ...muted, fontSize: 12 }}>{S.saved(saved[candidate.design_token])}</span>
              ) : (
                <>
                  <div style={{ flex: 1, minWidth: 140 }}>
                    <Input
                      aria-label={`${S.saveAs} ${i + 1}`}
                      onChange={(e: ChangeEvent<HTMLInputElement>) => setNames({ ...names, [candidate.design_token]: e.target.value })}
                      placeholder={S.saveAs}
                      value={names[candidate.design_token] ?? ''}
                    />
                  </div>
                  <Button
                    disabled={!(names[candidate.design_token] ?? '').trim() || busy !== null}
                    loading={busy === candidate.design_token}
                    onClick={() => void save(candidate)}
                    size="xs"
                  >
                    {S.save}
                  </Button>
                </>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
