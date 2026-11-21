# Synchronized sequence and plasmid workspace

strandfolio opens GenBank, FASTA, SnapGene `.dna` and explicitly typed text records in independent tabs. Each tab has synchronized sequence tracks, a coordinate overview, a circular or linear map, a feature list and selection details. Inspect individual feature segments or whole multipart features, including aligned complementary bases and computed CDS codons. The viewer supports DNA, RNA and protein, preserves FASTA letter case and reports GenBank case normalization. Figure exports protect the original sequence files.

## Launch

With the desktop wheel installed, run `strandfolio` from its Python environment. For the macOS arm64 source checkout, create the project-local environment.
[Environment setup](installation/README.md)

From the repository root, launch the desktop:

```sh
.venv/bin/python -m strandfolio
```

Pass a sequence or saved `.vibedigest` path to open it directly. This command opens the annotated pUC19 map shown in the README.

```sh
.venv/bin/python -m strandfolio databases/plasmids/maps/pUC19.gb
```

A new process loads updated code. You can run the launch command in a second Terminal window while a draft stays open in the first instance. Choose **Open design example** or run `.venv/bin/python -m strandfolio --example` to inspect the synthetic 4,500-base `pReporter_demo` with the plate’s six annotations and bases 929–988 selected. **Open example** loads the included annotated GFP plasmid. **Open sequence…** opens your own file.

On macOS, launch from Terminal in your logged-in desktop session. A process without a desktop session exits with a message before initializing Cocoa. The normal application uses the native Qt platform. `QT_QPA_PLATFORM=offscreen` is only for automated checks; it will not show a desktop window. The existing `python -m strandfolio.gui.main` route delegates to the same application.

## Open, switch and close records

- Choose **Open…** for GenBank, FASTA or SnapGene. One valid record opens directly. For a new multi-record file, review the originals in source order, including the ordinal, length, molecule type and available warnings. **Open N sequences** appends the batch and selects its first record. **Cancel** or an invalid record leaves the current tabs and their state intact.
- For a headerless `.txt` file, choose **File → Open sequence…**, select the file and choose DNA, RNA or protein. If it contains spaces, tabs or line breaks, review the preview before opening. The project retains the original file bytes. A FASTA header under `.txt` still opens as FASTA through ordinary Open.
- Choose **File → Paste sequence…**, paste from the clipboard and select DNA, RNA or protein. If the paste contains spaces, tabs or line breaks, review their removal before opening. A project keeps the captured text and the selected interpretation.
- Choose **File → Import independent copy…** to open an independent occurrence of a source you already opened.
- Click a record's name to activate it. The strip uses source or record details to distinguish duplicate labels. When the strip overflows, use its arrow controls or scroll horizontally to see more tabs. Tab focus and the strip's arrow, Home and End keys reach the names. Enter or Space activates the focused record. Scrolling the strip preserves the active record.
- Switching brings back the selected bases, sequence viewport, map zoom, pan and rotation, and the document's feature filter. Copy, **Import details** and **Export figure…** target the active record. **Import details** explains the detected format, inferred or ambiguous FASTA types, and available fidelity warnings. The [import guide](importing_sequences.md#run-the-example) describes the optional Python and CLI type override.
- **Close** targets the active tab. Use a tab's own close control to close it directly. An inactive close keeps the active record. An active close selects the tab to its right, or the one to its left at the end of the strip. Closing the final tab clears document actions while leaving the workspace and project open.

## Save and reopen project records

Use **File → New Project…** to create a project in a new or empty directory, or **File → Open Project…** to open an existing project directory. **Save as New…** gives an unsaved import or edited draft a unique name in the project. Editing a saved record creates a child with its own record ID. From a standalone workspace it first asks for a project destination. A project keeps the captured source bytes and the exact revisions. Move or back up the complete directory. [Project storage and backup](importing_sequences.md#save-and-reopen-a-project)

Use **Open saved sequences…** to open a named revision or an exact registered source snapshot. If a source was saved under a different name, both snapshots are available. Opening an already open clean revision selects its tab. If you have edited an unnamed import, choosing its registered original opens a separate clean tab. You can return to the draft to undo, redo or save your edits. Reopening a closed record starts with a fresh selection and viewport. **Open…** on the same source reuses the recorded intake when its origin, bytes and interpretation match.

For an unsaved sequence, **Discard** closes it without saving, **Keep open** cancels the close, and **Save as New** saves a named record before closing. The original source stays available in the project if it was already registered. **Close Project** and **Quit** show one unsaved sequence at a time and apply your choices after you review them all. If a later save fails, all tabs stay open and the earlier successful saves remain committed. Pending discards are applied only after every required save succeeds. Reopening a project starts with an empty strip. Open the committed records you want to inspect.

## Export a saved sequence

Activate the saved revision and choose **File → Export sequence…**. Choose GenBank for an annotated DNA record, FASTA for sequence plus a header, or plain text for the sequence letters. Review the reported fields and any losses for the chosen format before confirming publication. The project registers the export against the exact revision. To export a draft, save it in the project first. [Exchange formats and fidelity](importing_sequences.md#annotated-genbank-and-sequence-only-exports)

## Retrieve the original file

Activate a record with a captured original in the open project and choose **File → Retrieve original…**. Choose a destination folder. strandfolio verifies the project’s stored bytes and saves the entire captured source under its original filename. For a multi-record import, the recovered file contains the full batch. If the destination already contains that filename, choose a different folder. The old source file can be missing. A clipboard capture is recovered as `Pasted sequence.txt`. To reopen it, select its original molecule type when prompted. [Source capture and exchange](importing_sequences.md#retrieve-a-registered-original)

## Change a display name

When two copies of Strandfolio share a project, returning to a saved record refreshes its preferred name and genetic code. If someone changes a value while you are editing it, Strandfolio shows the newer value so you can review it before applying another change.

Hover over a tab or map title to reveal its names. Keyboard focus shows the same names in the status bar. Press F2 to inspect or copy a name that is wider than the window. A long label keeps the start and end when it fits with an ellipsis. Double-click the name itself or press F2 on the focused tab or map to open **Edit preferred name…**. From a focused tab, the context menu offers the same action. The editor shows the original name or header as selectable text. **Apply** updates the display name, **Clear preferred name** restores the record's own label, and **Cancel** keeps the existing name.

A preferred name follows the record through project reopen. In a standalone workspace it is carried into the first successful project save. Multiple records can use the same preferred name. The tab, map and exported figures share the display name, while GenBank and FASTA exports use the revision's exchange metadata. Renaming the display preserves the current selection and revision. [Exchange names and headers](importing_sequences.md#annotated-genbank-and-sequence-only-exports)

## Inspect a sequence and synchronized map

1. In the top action bar, use **Export figure…** to preview and save the map or its sequence tracks. See [Export a figure](#export-a-figure).
2. Sequence shows the tracks on the left and a whole-molecule map in the right inspector at wide sizes. Choose **Expand** on the small map to view it on the main canvas. **Back to tracks** restores the sequence. The view controls in the footer also switch between **Split**, **Tracks** and **Map**. Selecting bases shows their range and count in the footer. Choose **Selection** or **Go to…** to open the range controls and genetic-code panel. At narrow widths, **Tools** groups Go to, Enzymes and Layers.
3. Below the map, **Digest** and **All features** share the inspector. Collapse **All features** to give Digest more room. Under **⋯** (Feature options), use **Find features…** to filter by name or type, or **Key features only** to hide `primer_bind` entries.
4. Toggle **Features**, **Complement**, **Translations**, and **Primers** in **Layers** beside the workflow tabs. Primers start hidden. DNA and RNA initially show the stored sequence 5′→3′ and its calculated complement 3′→5′. Turn off **Complement** for a single row. The complementary row is a display setting independent of the molecule’s declared strandedness. Complement/translation controls are disabled for proteins. Use **−** and **+** beside the editable size field to adjust sequence letters from their 10 px default. Coordinates, aligned complementary bases and amino acids share the same base grid. Each complete mapped codon has a centered residue, including a complete codon next to incomplete trailing bases.
5. For a circular record, choose **Options** in the compact map to reveal its controls. Use **− / 1.0× / +** or the mouse wheel to zoom up to `8.0×`; when zoomed, drag away from the backbone to pan. Toggle **All labels** to expand individual labels from visible layers; crowded labels can overlap. Use `↺ 45° / ↻ 45° / 0°` to rotate. The compact map aligns landmark names on either side of the ring. A fine line connects each outside label to its feature. Expanded maps show intervals alongside the names when space permits. At overview (`1.0×`), the map groups consecutive repeats (such as `Label (×24)`). Zoom progressively expands repeats and primer labels.
6. Click a colored overview feature to select it. Drag on the overview, across bases or around the circular backbone to select a range. The slider beneath the overview scrolls the tracks while preserving the selection. Focus the slider and use Home or End to reach either end of the record. Alternatively enter **From / Through** and choose **Select range**: displayed base/residue coordinates are one-based and inclusive; internal intervals are zero-based and half-open. On circular records, **From > Through** or a clockwise drag across the origin selects wrapping segments. At a caret, both fields show the next base, or the last base at the end of the record. A feature crossing the circular origin shows the same endpoints on either strand. If a selection has gaps, both fields show a dash. Enter both endpoints to replace it with a continuous range.
7. Choose **Copy…** in the Sequence toolbar to preview a transformation of the selected bases. **Copy sequence** and the system Copy shortcut copy the stored letters directly. **Copy rev-comp** copies the reverse complement. Toggle **Details ▴ / ▾** to inspect the selection, feature qualifiers and source information.

| Control | Behavior |
| --- | --- |
| Arrow keys | Move the caret; Left/Right collapse a selection to its corresponding boundary |
| Shift + arrows / Shift + click | Extend from the existing anchor |
| Home / End | Start/end of the displayed row; Control or Command with Home/End targets the full sequence |
| Page Up / Page Down | Move through the sequence by a viewport of rows |
| Select All / Copy | Standard OS shortcuts when sequence has focus |
| Escape | Collapse selection to the caret |
| Double-click | Select the containing 10-letter block |
| Drag beyond top/bottom | Scroll while extending selection; release/focus loss stops it |
| Text size | Resize letters; keep selection and its caret visible |

## Copy a selection and change its genetic code

Select bases, an entire feature or one of its exact segments. Open **Copy…** and choose a format. The preview stays open after a copy so you can copy a second format from the same selection.

| Copy format | Text placed on the clipboard |
| --- | --- |
| Stored DNA, RNA or protein | Original letters and case from the selected intervals in their recorded order, with gaps omitted. |
| Complement | Aligned complementary DNA or RNA in 3′→5′ order. Selecting DNA `ATGC` gives `TACG`. |
| Reverse complement | Complementary strand in 5′→3′ order. The same selection gives `GCAT`. A selected reverse-strand feature follows its declared part order. |
| Transcribed RNA / Back-transcribed DNA | The stored coding strand with T changed to U, or U changed to T. The conversion preserves case and ambiguity. |
| Translated protein | Residues for codons wholly inside the selected intervals, in coding order. Stops appear as `*`. The preview reports excluded bases. |

Choose **Selection** in the footer to open **Genetic code**, including when the map inspector is hidden. Search by table number, name or alias. **Use annotated codes** honors each CDS’s explicit `/transl_table` and uses table 1 for a missing qualifier or a free frame. Choosing a table changes the document default. To apply it to explicitly annotated CDSs as well, enable **Preview this code for annotated CDSs**. The interpretation then identifies both tables. Choose **Use annotated codes** to restore the original interpretation.

**Translation track** lets you select an annotated interpretation or one of the six free reading frames. Each free frame starts at the 5′ end of the full record on the chosen strand. In the Copy dialog, **Translate** selects an overlapping CDS or a free frame. For example, with DNA `ATGTGACTGTAA` selected, forward frame +1 reads `M*L*` under table 1, `MWL*` under table 4 and `M*S*` under table 12. The selected intervals and their codon centers stay in place.

On an overridden CDS track, `Table 4 (1)` gives the effective table first and the annotated table in parentheses. Compact tracks and figures may show `4 (1)`.

The selection summary in the footer updates its CDS information when you change the genetic code, including when **Details** is collapsed.

A feature that was already partial stays marked partial after another PCR or digest. If opening a circle separates two adjacent parts of an unstranded feature or a feature with uncertain strand traversal, the resulting pieces are marked partial. Older saved results retain their recorded annotations.

The code preference follows the record across tab switches and project reopen. Copy and preview preserve the revision and its exported GenBank qualifiers, including the imported `/translation`. Protein records offer stored copy only. For an unsupported CDS, **Selection / CDS** and the protein preview explain why translation is unavailable. When a reverse-strand SnapGene feature crosses the circular origin, its imported part order may be uncertain. The whole-feature reverse complement and CDS translation report that limitation. Individual mapped parts remain selectable. [Import fidelity](importing_sequences.md#supported-inputs-and-fidelity)

## Edit annotations and review detected labels

1. Select bases in Sequence and choose **Annotate** in the Features sidebar. Enter the label, feature type and strand. Coordinates are one-based and inclusive. For a multipart location, enter one range per line in biological order. Split a circular origin crossing into two ranges. You can also supply a stored feature color and qualifiers. Choose **Apply** to create the feature.
2. Select a feature in the list, map or sequence track. In **⋯** (Feature options), **Edit label…** identifies the qualifier supplying its displayed name. For a fuzzy or remote location, use the feature list to edit its metadata while retaining the imported location. From the same menu, choose **Remove annotation…** to confirm the feature and its location. The **Features** and **Primers** layer switches only change the display.
3. On a DNA record, choose **⋯ → Detect labels**, then **Review labels** when the scan finishes. Each exact match shows its location and strand. An imported equivalent is marked **Already annotated**. **Accept all new** adopts unambiguous new matches. Review overlaps individually, or use **Dismiss** to exclude a match from bulk adoption. **Reconsider** explicitly adopts a dismissed match. A complete circular reference match appears separately in the review.
4. Use **Edit** to undo or redo annotation decisions. Undoing the first edit to a saved record restores the parent. **Discard annotation draft…** returns to the saved parent in the same tab. Choose **File → Save as New…** to publish the current edits. After restarting, **Open saved sequences…** lets you inspect the named child and original parent independently.

A scan alone preserves the current revision and undo history. Editing, closing or switching the active record invalidates a pending scan for that record. A dismissed or removed matching occurrence stays excluded on rescan. Accepted matches carry catalogue and exact-match evidence into the saved revision. [Released catalogue](../databases/features/README.md)

## Inspect imported annotations and CDS translations

For an annotated file, the right **All features** list shows a swatch, the name, the source interval and the strand. **⋯ → Find features…** searches by name or type. **Key features only** excludes `primer_bind`. Clicking a feature in the sidebar, a directional arrow in the sequence tracks, or an arc/label on the map selects all exact segments of that feature (excluding gaps between multipart segments) and scrolls the sequence view into view. Use the disclosure button beside a multipart feature to show its **individual segments**. The feature list supports keyboard navigation and pages long lists so that each displayed row remains reachable.

The inspector shows the computed protein and its agreement with the imported `/translation`. Exact local joins retain their declared part order, strand, `/codon_start` offset and source coordinates. The ordinary codon assignment applies even at the start of an annotation. A terminal `*` may be omitted from the imported qualifier during comparison.

The inspector flags internal stops, ambiguity, incomplete trailing codons and source disagreements. Unsupported interpretations include remote or fuzzy parts, mixed or unknown strands, unsupported location operators, unknown tables, context-dependent stop tables and `/transl_except` recoding. The full imported qualifiers remain visible in **Feature Qualifiers**.

## Export a figure

In Sequence, choose **Export figure…**. Pick the map, **Sequence detail · current region**, or **Sequence detail · full record**. Current region uses the selected intervals in order. With no selected bases it uses the visible sequence rows. The preview identifies the record, revision and scope. Use the width control to see the resulting dimensions before choosing a destination.

- **Map.** The circular map keeps the displayed angle, zoom, annotations and selected interval. On wide maps, names that fit follow the feature arrows. Short, crowded or selected features use outside labels. Select a whole-record source annotation on the backbone or in the feature list. The map title uses the preferred record name and fits the requested width. A linear map exports every lane included by its current feature and translation settings. For a circular record you can also display its stored sequence on a linear axis starting at position 1. Switching the representation or resetting the rotation preserves the underlying coordinates.
- **Sequence detail.** The figure uses the active base, complement, annotation, primer and translation tracks. The selected intervals retain their source coordinates, including intervals on either side of the circular origin. Recognition marks and supported cut boundaries come from the current restriction overlay.
- **Gel.** Choose the export control in Gel, a recorded Digest, or an exact PCR result. The preview uses the selected marker, lane order, co-migrating contributors and recorded run. Gel also offers pinned data as JSON. See [Digest a sequence](#digest-a-sequence-and-reopen-its-gel) and [Simulate PCR](#simulate-pcr-with-existing-primers).

Choose SVG for a vector figure or PNG for an image at the displayed pixel dimensions. PNG output is limited to 24 million pixels. For very long or densely annotated sequence detail, the preview estimates the number of drawing items before generating the vector scene. Above 30,000 estimated items, select a smaller region, hide unnecessary tracks or choose the full map. The writer rechecks the chosen destination before publication. If another file appeared or the chosen file changed while the figure was being prepared, choose the destination again. Canceling or encountering a write error preserves an existing file. Source originals, their filesystem aliases and managed project files are protected.

## Digest a sequence and reopen its gel

The installed wheel builds its enzyme catalogue on first use. For a source checkout, build the local catalogue. If a lookup fails, correct the reported problem and choose **Retry enzyme catalogue** in the Digest panel. [Catalogue setup](installation/README.md#reference-data)

You can preview a digest of either built-in example or an unsaved DNA sequence with known linear or circular topology. A draft’s results stay in the current session. Choose **Save digestion…** if you want to keep a file. Saved project sequences also keep their runs in project History. You can switch tabs while a project digest saves. A result that finishes after you switch stays with its source. Selecting a fragment or ladder updates the preview immediately. If the write fails, the preview returns to the last saved choice and reports the error.

Each sequence tab remembers its recipe and highlighted enzyme, even when two tabs have the same display name. **Remove** applies to the highlighted enzyme in the active tab. After removing it, select another row to remove another enzyme. If you leave Sequence while a digest is running, the status bar identifies the source and outcome. An unsupported reaction gives its reason. A successful zero-cut reaction leaves the molecule intact and can be reopened in History.

1. In the **Sequence** sidebar, choose one or more enzymes from **Add digest enzyme…**, then click **Digest**. The preview shows the fragments and their expected gel bands. **Cancel** is available while the calculation runs. The recipe applies to the whole molecule. You can also add an enzyme by clicking its site on the sequence or map, or by focusing a site with F7 or Shift+F7 and pressing Enter or Space. The **Enzymes…** display filter controls which sites you see.
2. Select a fragment row or its gel band to highlight its exact source ranges on the labeled linear map at the top of the popup. A fragment crossing the circular origin highlights both ends of the map. Use **Products** to choose among fragments that share a band. The details show strand lengths, ends and inherited features. In a compact popup, scroll the results below the map to reach the details and gel. **Copy fragment** copies the physical forward strand in 5′→3′ order. **Create construct** opens the selected cut fragment as a draft. Enter a name in Sequence and choose **Save construct**, or **Cancel** to discard the draft. A completed zero-cut reaction keeps the input topology.
3. Choose a **Ladder** in the result preview, then **Add digest to Gel**. Products of equal nominal size share a band and retain individual output keys. The nominal size is the forward-strand length. Uncut circles appear in the inventory. The vertical scale shows illustrative migration for linear DNA. It covers 100–10,000 bp by default and uses the same transform for the marker and samples.
4. Close the source tab and use the pinned lane to reopen its exact source snapshot or recorded digestion. **History** lists runs for the selected revision. After restarting, open the project and the source revision to see that list again.
5. **Save digestion…** keeps the source snapshot, enzyme definitions, products, selected contributor and marker profile together. If you leave off the extension, strandfolio adds `.vibedigest` and asks before replacing an existing file with that name. The writer validates a staged copy and checks the destination again before publishing. If the destination appeared, changed or disappeared during staging or review, the save stops. Choose the destination again to retry. Use **File → Open sequence…** or **History → Open saved digestion…** after moving the file. The reader also follows symbolic links to regular files. Reopening restores the same run. **Run again…** opens the recorded inputs for review, then confirmation starts a calculation with a new run ID.

If a marker list fails to load, choose **Retry ladder catalogue** beside the ladder controls in Gel or in a Digest or PCR preview.

In **Gel**, lane numbers identify samples on the canvas. Turn on **Band sizes** for numeric labels. Select a band for its contributors, or expand **Source details** to inspect the selected lane. The lane name remains next to **Replace…** and **Remove** while scrolling. When the pinned source is unavailable in **Replace…**, choose a replacement to enable **Save lane**.

A portable digest from an annotation-edited template carries the applied annotation steps and the exact source snapshot in version 2. Digests without those steps keep the version 1 format. Strandfolio reads and writes both file versions up to 64 MiB, counting the complete recording and its ancestry. The Python functions `readDigestion` and `writeDigestion` accept a larger explicit `maxBytes` for larger recordings. An oversized save preserves the existing destination.

## Simulate PCR with existing primers

Strandfolio opens PCR on the active document. Select primer sites on the sequence, enter an existing pair or load two records from FASTA.

1. **Choose a primer.** Drag over the bases for one primer, then click **Select forward primer** or **Select reverse primer**. You can also click the button first and watch the **nt** count as you drag. Release to fill that field. Its temporary selection clears. A green band marks the forward section and an amber band marks the reverse section, including each piece when a site wraps onto another line. Repeat for the other primer in either order. For a product running left to right on the top strand, use the left stretch for forward and the right stretch for reverse. Strandfolio reverse-complements the reverse stretch so both fields read 5′→3′.

   At **61–80 bases**, the editor warns about the length and accepts the selection. At **81 bases or more**, choose a shorter stretch or use the larger region as the PCR target. You can also type a primer or use **Load FASTA…**. Press **Esc** to cancel a pick. To dismiss a site comparison, press **Esc** on the canvas or click an unmarked base. Click a marked section to inspect it again. If it is offscreen, click **F** or **R** on the overview to jump to it. Choosing an assigned role in the comparison menu also brings its site into view. Use the **×** in a binding field to remove that primer and its 5′ end, or **Clear draft** to remove both primers from this document. Pairs you explicitly saved are available in **Saved pairs…**.
2. **Review the selected site.** Above the sequence, the primer reads 5′→3′ over its template strand, which reads 3′→5′. Edit **5′ end** there to see the added bases immediately. A vertical bar marks a match, **×** marks a mismatch, and **~** marks an unpaired end. You can also edit the same end beside its primer in the sidebar. A solid edge marks a picked site that matches the binding field. Editing that field can leave a lighter band with a dashed edge at the original site. The sidebar reports exact matches for the current primer. If an edit changes its length, the comparison shows both lengths. The PCR simulation still uses exact matches.
3. **Test and revise.** On DNA with known linear or circular topology, choose **Simulate PCR**. Inspect a product, then close the result to edit and run again. Returning to the same document and revision restores its selected product. After an input change, **Show previous result** opens the earlier run in its own preview. To locate an earlier binding site on the canvas, use **Show forward binding** or **Show reverse binding**. The canvas identifies that view as a **Recorded product**. From the result or its History entry, choose **Use pair in editor** to reuse its original pair. Review the destination if Strandfolio asks to replace an existing draft.
4. **Keep alternatives.** In **Saved pairs…**, choose **New pair…** to name the editor’s pair. Select an entry to edit its name or either primer, duplicate it, delete it, or apply it to the active editor. After editing or testing a draft, select its saved entry and choose **Update selected pair from editor…**. Review both members and save to keep the new revisions under the same pair name. Entries may share a display name. Deleting an entry preserves the oligos in previous runs. With a project open, the list belongs to that project and survives reopening. Without a project, the list lasts for the current session.

**Exchange a pair as FASTA.** Choose **Save FASTA…** before or after simulating. In a recorded result, **Save primer pair…** exports the exact pair captured by that run. Each record stores the full oligo in its own 5′→3′ direction, with its name, role and tail length in the header. **Load FASTA…** previews both records, including pairs from `.fna` files. For ordinary two-record FASTA, choose the forward record and review the tail lengths, initially zero. The reverse oligo keeps its entered orientation through export and reload. If the destination changes while you confirm a save, choose it again before retrying.

Erasing both fields for a primer clears that draft member’s name and identity. An ordinary edit preserves the member’s identity, and previous runs retain their captured pair. Selection feedback describes the highlighted range until you choose a primer role, then checks that role’s 5′ end.

**Check coverage of a gene or range (optional).** Right-click the gene on the overview or its feature bar to mark it as the PCR target, even after selecting other bases. For a highlighted range, choose **Set selection as PCR target**. The pinned target appears above the sequence. Pick another primer freely, replace the target with another gene or range, or right-click and choose **Clear PCR target**. Each result reports how many target bases its product covers.

**Review full primers** shows each complete oligo in its own 5′→3′ direction with its optional end first. For a structure-only check, you can enter the complete sequence once in the binding field and leave the 5′ end empty. For PCR, enter the annealing portion and 5′ end in their respective fields.

**Primer quality** shows the estimated melting temperature (Tm) and flags possible hairpins or dimers as you edit. You can start with one primer. Adding the second enables the pair-dimer check and shows the Tm difference when the estimates use the same model.

Choose **Show checks** to see the individual findings and predicted structure temperatures. Expand **Calculation details** for model settings and inputs. Tm uses the annealing section and is approximate at 61–80 nt. Structure checks use the complete primer, including its 5′ tail, up to 60 nt. In a recorded result, **Assay checks** uses the primers saved with that run. [Methods and limits](validation.md#primer-structure-checks)

During the search, **Cancel** replaces **Simulate PCR** in the top toolbar.

1. The result reports the number of exact-match products in the recorded size range on the captured template. It warns when ambiguous positions were excluded from binding. A completed search can have zero products. A capped or unsupported search reports its reason. For a stopped attempt in the active PCR view, the inspector still checks the captured oligos and leaves template site and product counts unassessed. The exact search covers both orientations of the pair and requires each full annealing segment to match.
2. Select a product row to inspect its sequence and ordered source intervals. Change **Ladder** to compare against another marker. Products of equal length share a band and keep separate rows. The gel illustrates product sizes in bp. A band’s brightness carries no yield estimate. **Copy sequence** copies the selected mature product, including its tails. **Add product to Gel** pins only that output.
3. **Open product** opens a draft with the recorded source and primer identities. Equal-size PCR products retain their own output keys. To revisit a result, open its exact source from Gel or select the run and output in **History**. An open project retains recorded PCR attempts across restarts. In History, select a stopped attempt to inspect its reason. Product actions require a completed output. If the project write fails, **Retry project save** writes the retained result with the same identities. Without a project, the result is held in the current session.

The desktop support table lists the search limits. The validation register reports the results for the fixed example pairs. [Desktop support](#desktop-support) · [PCR validation](validation.md#pcr-and-melting-temperatures)

## Choose Assembly inputs

Select **Choose backbone…** or **Choose insert…**. The source inspector offers linear DNA and DNA with unspecified topology, including eligible digest and PCR outputs. **Open file…**, **Open saved…** and **Paste sequence…** use the workspace intake controls. Opening a source returns you to the picker. **Assign source** confirms it for the chosen role. Use **Add insert…** for another occurrence. These choices are kept for the session. Computing a new joined product from these inputs remains planned.

Each filled card shows its source name, length, topology and orientation in the plan. Hover over a shortened name to read it in full. Select the occurrence in **Input order** to inspect or change it. Choose **Open source in Sequence** to reopen its exact source revision. For a prepared Digest or PCR output, this opens the template recorded by that run. Returning to Assembly restores the same pins and order.

After assigning the first source, open **Planned junctions** in the inspector. Each row names two consecutive roles. The last row returns to the backbone. Select a row to see the pinned revisions, each role’s chosen orientation and any recorded preparation and output IDs. An empty role says **No source selected**. Reusing one input in several slots gives each occurrence its own place in the list.

When an input has duplex geometry, the inspector shows its recorded end types and the inclusive span occupied by each strand on the input record. An extending strand can reach beyond its partner on that same input. Flipping the role changes the plan orientation and leaves those source coordinates intact. **Pairing between these inputs has not been assessed** applies to the proposed junction, including when both inputs have recorded ends. [How software represents partial complementarity](validation.md#partial-complementarity-in-assembly-software)

### Save the illustrated product

In **Assembly**, choose **View design example**. The two-color circle shows the source contributions printed in the plate for the existing 4,500 bp reporter. Choose **Show annotations** for the map drawn from its six GenBank features. **Back to inputs** returns to your working plan.

Choose **Save named copy…**, enter a name and choose a project folder if prompted. The copy opens as a clean document while Assembly stays visible. **Saved copies** lists this session’s copies newest first. Choose a copy, then **Open in Sequence** or **View in History**. After restarting, choose **File → Open Project…**, then **File → Open saved sequences…** to reopen it. **History → Source attribution** displays the two source names credited in the GenBank file.

## Inspect recorded ancestry

Choose **History → Recorded analyses** to see saved runs for the active sequence revision. If two copies of Strandfolio have the same project open, revisit this page to see the other copy’s newly saved runs. Each window keeps its own open tabs, selections and Gel lanes.

If a Digest or PCR project save fails, switch back to the sequence where you ran it and retry the original save. If you have closed the source tab, open **History → Recorded analyses → Retry project save…** in the same session. Choose the source, revision and run you want to recover. For a sample prepared in Gel, **Retry gel sample save…** identifies the sample before you add its saved result to a lane.

In History, choose **Ancestry** in the toolbar to see a diagram of the recorded revisions and their inputs. Select a molecule on the diagram or use the keyboard-accessible revision and input lists. For an adopted digest or PCR product, the inspector shows its operation, output key and recorded contributions. Coordinates in the inspector use 1-based inclusive ranges. Select a revision, set **From** and **Through** to the product bases of interest, and choose **Trace to original sources**. The trace follows previously recorded intervals and shows primer contributions separately. **Show recorded intervals** returns to the immediate inputs. Choose an available ancestor and **Open source snapshot** to activate its exact revision in Sequence. Gaps identify the reference that could not be resolved. **Recorded analyses** lists runs performed on the active revision.

When an earlier operation is missing, the input row and inspector identify the gap. You can still open an ancestor whose exact snapshot is available. A conflicting snapshot is marked unavailable and cannot be opened from that link. If more records arrive in the project, choose **Recheck ancestry** to read the evidence again for the same revision. If the ancestry read fails, **Retry ancestry** repeats it. If saved PCR runs or their preview settings fail to load, choose **Retry PCR history** in the History toolbar. Successfully loaded runs stay available during the retry.

### Document an existing construct

Save the finished record and its known parents in the same project. Activate the finished record and open **History → Reported parents**. Choose the exact saved parents and enter any source and child intervals you already know. The fields use 1-based inclusive positions. You can leave intervals or direction unknown, record a source more than once, and mark a known direction as forward or reverse. The map shows the supplied locations on the child. Reopening a parent selects the exact saved snapshot.

These links record your account of the construct’s history. The project keeps them apart from operations strandfolio executed. **History → Ancestry → Export provenance…** saves a JSON report for the active revision. It includes recorded inputs, unresolved evidence and separately attributed parent links. The JSON report uses zero-based, half-open intervals. To restore a project with its original files and full records, use the [project backup](importing_sequences.md#save-and-reopen-a-project).

If the provenance destination changes while the export is being prepared, choose the destination again to retry.

## Compare open sequences

1. Open at least two records of the same molecule type. Activate the reference and choose **Alignment**. Check the queries under **Compare with**. Expand **Pairwise settings** to choose global or local alignment. Type a score directly, including fractional values, or step it with **−** and **+**. DNA and RNA allow a whole record or an exact, known-strand gene, CDS, ORF or replication origin, with an orientation for each input. Protein uses each whole record in its stored order.
2. Choose **Run alignment** at the top of the inspector, then select a query in **Results**. The run controls stay visible as you scroll, and **Cancel alignment** occupies the same position during calculation. Identity counts exact matches among columns containing two nucleotides or two residues. Reference coverage divides that paired count by the selected reference length. A three-residue exact match in a nine-residue reference has 100% identity and 33.33% coverage. Protein substitution scores use BLOSUM62. A favorable substitution still counts as a difference.
3. Select a difference or alignment column to inspect its original coordinates. At a gap, the aligned symbol keeps its source coordinate and the opposite side shows the boundary between positions. For DNA and RNA, translation follows the ungapped input. In the source frame, a CDS uses its annotated genetic code when supported. Each row identifies the code or the reason its source translation is unavailable. The six explicit frames use the standard code. An overlapping set of possible amino acids gives an unknown codon effect. Choose **Off** to hide translations. Protein tracks display the original residues.
4. Activating a record in the document strip opens **Sequence**. Return to **Alignment** to inspect the same comparisons. Closing one query removes its live comparison. Closing the reference clears the live set. Keep a completed comparison to inspect its original inputs and settings during the current workspace session.

DNA and RNA retain their own alphabets. RNA reverse complements use U. Pairwise protein comparisons accept the BLOSUM62 alphabet, including B, Z, X and `*`. Records containing J, O or U can be viewed in Sequence, but these residues have no score in BLOSUM62. Each selected input is limited to 20,000 symbols and each pairwise matrix to 25,000,000 cells.

To compare all selected rows in one matrix, check two to eight queries and select **Align all together**. Kalign builds a global multiple alignment. The left edge names each sequence and identifies its role. Gold marks differences from the reference and violet marks gaps. Click or drag the overview beneath the matrix to jump along the selected row. Use the arrow keys or enter a number in **Go to column** for an exact position. The selected cell’s source coordinate or gap appears below the matrix. **Export aligned FASTA…** saves the complete matrix for an alignment reader. The desktop’s **Open sequence…** accepts ungapped FASTA and cannot reopen a saved alignment as a matrix. The dialog closes if its selected inputs change.

Shared-column runs accept up to 20,000 symbols per row and 80,000 overall. A run stops if the summed sizes of the pairwise matrices exceed two billion cells or the worker exceeds 30 seconds. Protein rows accept the 20 standard residues plus B, Z and X. Whole circular records start at their stored origins. To compare a homologous annotated region across plasmids, select that feature in each row.

[Alignment validation](validation.md#alignment)

## Supported limits and honest display

- `.fasta`, `.fa`, `.fas`, `.fna`, `.faa`, `.frn`, GenBank `.gb`, `.gbk`, `.gbff`, `.genbank` and SnapGene `.dna` use the shared intake adapter. FASTA content in a text file is also accepted. Headerless text requires an explicit molecule type. Selecting the plain-text filter requests literal text and rejects structured file content.
- GUI intake is currently bounded to **64 MiB of source bytes, 200 records and 2,000,000 total sequence letters** per file. The source-byte cap is independent of sequence length, allowing metadata-heavy SnapGene files larger than 5 MiB. These are initial product limits, not claims of genome-wide support. The Python reader's separate contract is in the [import guide](importing_sequences.md).
- Imports run outside the GUI thread. Failure or cancellation preserves existing tabs and selections. A bounded read already in progress may finish in the background, and its canceled or superseded result is ignored.
- FASTA does not supply structured annotations, topology or duplex geometry. GenBank and SnapGene can supply topology and supported parsed annotations, but not complete source fidelity; see [the import limits](importing_sequences.md#supported-inputs-and-fidelity). The straight overview is a coordinate axis, including for circular records. It does not assert a linear molecule. Lengths are `nt` or `aa`, not inferred duplex base pairs.
- PCR displays exact primer footprints on its template. ORF prediction overlays and direct editing of sequence letters remain planned. Declared circular topology enables the circular map. Records with unknown topology use a labeled linear coordinate view.

Under **Layers → Map features**, DNA and RNA maps open with **Landmarks**. This shows genes, coding sequences, promoters, replication origins and terminators. A whole-record source appears on the backbone. **All features** enables every annotation type in the Features layer. **Choose types…** lets you include variations, exons, introns or other types present in the document. Primers have their own layer. Each open document remembers its map filter when you switch tabs. The sequence rows and full inspector list keep their annotations. Select a filtered annotation in the list to reveal it on the map for the duration of the selection. The exported map uses the same settings.

On a linear map, short annotations can carry a clickable name beside their arrow. The Landmarks view shows the selected CDS translation when **Translations** is on. With **All features**, the annotated translation tracks are available for every supported CDS. An explicitly chosen reading frame still uses the frame track. **Hidden by type** counts annotations removed by the filter. **Below canvas** counts included segments or translation tracks that fall below the visible area. Choose **Expand** to see more or export the full height.

## Working in dialogs

Use the **Import details** info icon beside **Export figure…**, or choose **File → Import details…**, to inspect the source file and any reader notices. In Label Review, selecting a match updates its evidence. The list and evidence appear side by side in a wide window and stack in a narrow one. Long results and pending work scroll inside their windows so the action buttons stay within reach. In **Add lane**, expand **Restriction enzymes (optional)** to choose a treatment. The collapsed control shows how many enzymes you chose. In a close review, press **Escape** or choose **Keep open** to return to the workspace.

## Desktop support

The tested desktop configuration is Apple Silicon macOS with Python 3.12. The table covers the six workflows in the current source checkout. The scientific validation register gives the independent comparisons and inputs for each tested result. [Installation](installation/README.md) · [Scientific validation](validation.md)

| Workflow | Fields and limits | Examples and reference |
| --- | --- | --- |
| Sequence and project controls | Synchronized tracks and maps, exact single and multipart selections, independent tab state, reviewed batch intake, preferred names and exact named or source reopen. GUI intake applies the 64 MiB, 200-record and 2,000,000-letter caps together. Fuzzy, remote and unknown locations are inspectable. | [Workspace notebook](notebooks/09_interactive_workspace.ipynb) |
| Annotation curation | Create, rename and remove annotations, review exact matches against the released feature catalogue, undo or redo, and save a named child with its exact parent. Existing imported location evidence is retained when editing metadata. Sequence-letter editing and ORF prediction are excluded. | [Label review](#edit-annotations-and-review-detected-labels) |
| Copy and translation | Stored letters, aligned complement, reverse complement, DNA/RNA conversion, and protein preview for supported CDSs or free frames. Retain ordered codon coordinates, stops, annotated/effective codes and per-record preview settings. Unsupported locations and recoding report reasons. Protein offers stored copy. | [Conversion controls](#copy-a-selection-and-change-its-genetic-code) |
| Restriction scan and digest | The 1,088-entry Biopython 1.88 / REBASE 404 catalogue supplies lookup and recognition definitions. The current engine simulates ideal complete cleavage for 13 reviewed definitions on DNA with declared topology and records ordered source spans, physical strands, ends, annotations and output keys. Unsupported geometry and modification-dependent cleavage get diagnostics. | [Catalogue notebook](notebooks/03_enzyme_catalogue.ipynb) · [Reviewed enzyme definitions](validation.md#200-enzyme-panel) |
| Portable digest and recorded History | Reopen `.vibedigest` versions 1 and 2 offline with the source revision, model and definitions, fragments, selected contributor and ladder profile. Reopening uses recorded annotations and output identities. Rerunning creates a distinct result under the current engine. History traces recorded digest and PCR outputs through exact source intervals and reports missing evidence. | [Reopen a digest](#digest-a-sequence-and-reopen-its-gel) · [Recorded ancestry](#inspect-recorded-ancestry) |
| Exact PCR | The current engine matches two existing primers on one DNA template and maps each keyed product and its 5′ tails. Search limits are 1,000,000 template bp, 4,096 binding sites and 100,000 opposing pairs. Output limits are 256 products, 100,000 bp per product, 8,000,000 total product bp and 1,000,000 source-feature projections. Reaching a work limit returns a capped status. Complete oligos up to 60 nt have model structure checks. The editor estimates ideal-complement Tm for supplied annealing segments of 2–80 nt and labels the 61–80 nt model as approximate. General design, mismatch searches and reference-wide specificity remain planned. | [PCR validation](validation.md#pcr-and-melting-temperatures) |
| Gel | Pin untreated DNA, digest lanes and individual PCR outputs. DNA marker changes preserve lane identities and contributors, including equal-size products. The shared scale is illustrative, with a default 100–10,000 bp display range. Physical migration, intensity and protein gels are excluded. | [Ladder notebook](notebooks/08_ladders_and_gels.ipynb) |
| Alignment | Biopython 1.88 compares up to eight independent queries globally or locally. Kept pairs last for the session. Kalign 4.0.0 aligns 3–9 rows globally in one matrix and exports aligned FASTA. Both modes cover DNA, RNA and protein. | [Alignment validation](validation.md#alignment) |
| Assembly | Choose a backbone and ordered inserts for the session, inspect each planned junction and its source evidence, revisit the exact sources, and save a named copy of the bundled design. Assessing pairing and generating a new product with History remain planned. | [Assembly inputs](#choose-assembly-inputs) · [Saved example](#save-the-illustrated-product) |
| Figure export | SVG and PNG for circular and linear maps, selected/visible or full sequence tracks, and Digest, PCR and Gel previews. PNG is capped at 24 million pixels. Sequence detail is capped at 30,000 estimated drawing items. Cancel, failed publication and protected destinations preserve existing files. | [Export controls](#export-a-figure) |

For GenBank, FASTA, text, SnapGene intake and project recovery, see the
[exchange and project support matrix](importing_sequences.md#exchange-and-project-support).

## Notebook and implementation connections

Notebook 02 shows the pUC19 multiple cloning site in three map views, maps the larger Dryad GFP plasmid and compares codon translations. Notebook 09 follows an origin-spanning feature in the SB2CL plasmid from selection through a label edit, undo, redo and reopening the saved project. [Map notebook](notebooks/02_sequence_visualization.ipynb) · [Workspace notebook](notebooks/09_interactive_workspace.ipynb)

## Verification and native desktop acceptance

Maintainers with the local `tests/` and `dev/` directories can run the regression suite offscreen with the project interpreter.

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

Use a new output directory for the maintainer audit’s synthetic screenshots, rendering timings and local source checks.

```sh
PYTHONDONTWRITEBYTECODE=1 QT_QPA_PLATFORM=offscreen .venv/bin/python -m dev.audit_sequence_workspace \
  --output docs/notebooks/_outputs/U07/my_review \
  --sequence strandfolio/data/examples/synthetic_circular.dna
```

The audit saves synthetic screenshots, package/runtime hashes and a manifest. It opens the illustrated reporter at 1440 × 900 and 920 × 620. Its six-tab check captures a completed Alignment, two pinned Gel lanes and the loaded History view at 1440 × 900, clicks every tab at 940 × 620, and measures the glyph widths in the sequence grid. The audit also opens the Sequence import and copy dialogs, PCR target, Assembly named copy, Gel lane picker, Alignment reference list and History view list. It checks that canceling preserves the active record and selection. It verifies the shared document row, the left sequence canvas, the right molecule inspector and the active record after switching workflows. It does not save private sequence text. Ignore rules cover generated evidence; that is not a backup.

The local `planning/PROGRESS.md` records current stages, run links, source-integrity results and remaining execution limits. Audit timings describe their recorded inputs and environment, not native frame-rate guarantees. Large unannotated samples do not establish worst-case dense-CDS/feature performance.

Native acceptance uses the normal desktop. Record the observed dialogs, tab focus, map gestures and external paste independently of browser and offscreen results.

1. Launch normally and open GenBank, FASTA and SnapGene through the file dialog. Open a headerless text file, choose its molecule type and check its whitespace preview. Check cancellation and visible errors with an existing document open.
2. Select bases, origin-wrapping ranges and whole/individual feature segments in both views. Copy stored and explicit reverse-complement text into an external text editor and compare exact letters, case and orientation.
3. Exercise keyboard focus/navigation, native scrolling/trackpad behavior, circular zoom/pan/rotation, resize, compact/split modes and panel collapse. Check label readability and application/window branding at the actual display scale.
4. Open two identically named records, confirm a batch and force strip overflow. Switch away and back using the pointer and keyboard. Check restored selection, viewport, map state, copy and figure targets. Cancel batch review and try a malformed later record. Check canceled and failed exports and source preservation in both formats.
5. Close an inactive, active and final tab. Review the unsaved choices and a canceled project or window close. Reopen the project and confirm that the strip starts empty and the exact named and source snapshots are available. Confirm that late work cannot change a record after switching away and returning.
6. Resize long duplicate names, reveal the full and original names by keyboard, and edit from the strip and map. Cancel, reopen and clear a preference. Check accessible names, keyboard reachability and the existing selection routes.

For P7, use the normal desktop launcher from the selected installation. Open a saved
project, load its named revision and export a supported figure. Reopen a moved
portable digest and the same recorded run from its source History, then verify the
selected fragment and marker. Exercise the selected PCR, alignment, annotation and
conversion cases in the support matrix. Run Python sequence exchange and independent
project recovery separately. [Project recovery](importing_sequences.md#save-and-reopen-a-project)

Maintainers record desktop, offscreen, exchange and restore outcomes separately in their local release record.
